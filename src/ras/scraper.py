# src/ras/scraper.py

from __future__ import annotations
import asyncio
import json
import re
from typing import List, Dict, Any, Callable
from playwright.async_api import Page
from .models import RasQuery, RasListingItem
from .net import async_retryable


class RasScraper:
    BASE = "https://ras.arbitr.ru"

    # Update these patterns after Network inspection in DevTools → XHR.
    XHR_LIST_PATTERNS = [
        "/Search", 
        "/search", 
        "/GetDocuments",
        "/Cases",
        "/Documents",
    ]

    # CSS fallback selectors — must be verified/updated for live markup.
    SEL_INPUT_TEXT = "input[placeholder*='Поиск']"
    SEL_DATE_FROM = "input[name='dateFrom'], input[aria-label*='Дата с']"
    SEL_DATE_TO = "input[name='dateTo'], input[aria-label*='Дата по']"
    SEL_FIND_BTN = "button:has-text('Найти'), button[type='submit']"
    SEL_RESULTS_CONTAINER = ".search-result, #results, .results"
    SEL_RESULT_ROW = ".result-item, .result-row, .search-result .result-item"
    SEL_RESULT_TITLE = ".result-title, a.result-link, .doc-title a"
    SEL_RESULT_META = ".result-meta, .doc-meta, .meta"

    def __init__(self):
        # Optional: preload mapping dictionaries here (region/doc type normalization)
        self.region_map: Dict[str, str] = {}
        self.doc_type_map: Dict[str, str] = {}
        self.instance_map: Dict[str, str] = {}

    async def open_search(self, page: Page):
        await page.goto(self.BASE, wait_until="domcontentloaded")

    async def _safe_fill(self, page: Page, selector: str, value: str | None):
        if not value:
            return
        el = await page.query_selector(selector)
        if el:
            await el.fill(value)
            await asyncio.sleep(0.1)

    async def _safe_click(self, page: Page, selector: str):
        el = await page.query_selector(selector)
        if el:
            await el.click()

    async def apply_filters(self, page: Page, q: RasQuery):
        # Text query
        await self._safe_fill(page, self.SEL_INPUT_TEXT, q.text)
        # Dates
        await self._safe_fill(page, self.SEL_DATE_FROM, q.date_from)
        await self._safe_fill(page, self.SEL_DATE_TO, q.date_to)
        # TODO: court_region / court_id / doc_types / instance —
        # implement with actual UI selectors (comboboxes/checkboxes), confirm via DevTools.
        await self._safe_click(page, self.SEL_FIND_BTN)

    async def _intercept_json(self, page: Page, predicate: Callable[[str], bool]) -> List[Dict[str, Any]]:
        """Collect JSON payloads of XHR responses matching URL predicate during search."""
        bucket: List[Dict[str, Any]] = []

        async def on_response(resp):
            try:
                url = resp.url
                if not predicate(url):
                    return
                ct = resp.headers.get("content-type", "")
                if "application/json" in ct:
                    data = await resp.json()
                    bucket.append({"url": url, "json": data})
            except Exception:
                # swallow and continue
                pass

        page.on("response", on_response)
        return bucket

    def _predicate(self, url: str) -> bool:
        return any(p in url for p in self.XHR_LIST_PATTERNS)

    def _parse_list_json(self, records: List[Dict[str, Any]]) -> List[RasListingItem]:
        """Heuristic JSON parsing; adapt to the structure you observe in DevTools."""
        items: List[RasListingItem] = []
        for rec in records:
            data = rec.get("json")
            if not data:
                continue
            # Try common shapes
            seq = None
            if isinstance(data, dict):
                # plausible keys: results, documents, items
                for k in ("results", "documents", "items", "Rows", "rows"):
                    if k in data and isinstance(data[k], list):
                        seq = data[k]
                        break
                if seq is None and any(isinstance(v, list) for v in data.values()):
                    # first list-like value
                    for v in data.values():
                        if isinstance(v, list):
                            seq = v
                            break
            elif isinstance(data, list):
                seq = data

            if not seq:
                continue

            for row in seq:
                if not isinstance(row, dict):
                    continue
                items.append(RasListingItem(
                    act_id=row.get("ActId") or row.get("Id") or row.get("DocumentId"),
                    case_id=row.get("CaseId") or row.get("CaseID"),
                    case_number=row.get("CaseNumber") or row.get("CaseNo") or row.get("НомерДела"),
                    instance=row.get("Instance") or row.get("Инстанция"),
                    doc_type=row.get("DocumentType") or row.get("ТипАкта"),
                    court=row.get("CourtName") or row.get("Суд"),
                    court_code=row.get("CourtCode"),
                    region=row.get("Region"),
                    date=row.get("RegistrationDate") or row.get("Date") or row.get("Дата"),
                    title=row.get("Title") or row.get("Заголовок") or row.get("FileName"),
                    parties=row.get("Parties") or row.get("Стороны"),
                    detail_url=row.get("DetailUrl") or row.get("Url") or row.get("Link"),
                    download_url=row.get("DownloadUrl"),
                    extra=row,
                ))
        return items

    async def collect_listings(self, page: Page, q: RasQuery, limit: int = 100) -> List[RasListingItem]:
        """Run a search, intercept XHR JSON, and fallback-parse DOM.
        Returns normalized listing items (metadata may be partial if fallback).
        """
        bucket = await self._intercept_json(page, self._predicate)
        await self.apply_filters(page, q)
        # Wait for results container or some network idle
        try:
            await page.wait_for_selector(self.SEL_RESULTS_CONTAINER, timeout=15000)
        except Exception:
            # proceed anyway, we might have JSON
            pass
        await asyncio.sleep(0.5)

        json_items = self._parse_list_json(bucket)
        items: List[RasListingItem] = []
        items.extend(json_items)

        if len(items) < 5:
            # Fallback to DOM parsing
            rows = await page.query_selector_all(self.SEL_RESULT_ROW)
            for r in rows[:limit]:
                title = None
                meta = None
                detail_url = None
                try:
                    if await r.locator(self.SEL_RESULT_TITLE).count():
                        link = r.locator(self.SEL_RESULT_TITLE).first
                        title = (await link.inner_text()).strip()
                        detail_url = await link.get_attribute("href")
                    if await r.locator(self.SEL_RESULT_META).count():
                        meta = (await r.locator(self.SEL_RESULT_META).inner_text()).strip()
                except Exception:
                    pass

                case_number = None
                date = None
                court = None
                doc_type = None
                if meta:
                    # Heuristic regexes; refine for actual meta format
                    m = re.search(r"№\s*([A-ЯA-Z0-9\-\/]+)", meta)
                    if m:
                        case_number = m.group(1)
                    d = re.search(r"(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})", meta)
                    if d:
                        date = d.group(1)
                    dt = re.search(r"(Решение|Определение|Постановление)", meta)
                    if dt:
                        doc_type = dt.group(1)
                if detail_url and not detail_url.startswith("http"):
                    detail_url = self.BASE + detail_url

                items.append(RasListingItem(
                    case_number=case_number,
                    date=date,
                    court=court,
                    doc_type=doc_type,
                    title=title,
                    detail_url=detail_url,
                ))

        # Truncate and deduplicate by (case_number, detail_url or title)
        uniq, out = set(), []
        for it in items:
            key = (it.case_number or it.act_id or it.title, it.detail_url or it.title)
            if key not in uniq:
                uniq.add(key)
                out.append(it)
            if len(out) >= limit:
                break
        return out

    @async_retryable(max_attempts=4)
    async def resolve_download_url(self, page: Page, detail_url: str) -> str | None:
        """Open detail page and try to discover a direct document download link.
        Strategies:
          1) Intercept JSON/XHR while the page loads and scrape link from response.
          2) Look for anchor tags with .pdf or known download buttons in DOM.
        """
        bucket = await self._intercept_json(page, lambda u: any(x in u for x in ["/Download", ".pdf", "/Document"]))
        await page.goto(detail_url, wait_until="domcontentloaded")
        await asyncio.sleep(0.5)

        # Try JSON bucket for direct link
        for rec in bucket:
            data = rec.get("json")
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str) and v.lower().endswith(".pdf"):
                        return v
            elif isinstance(data, list):
                for v in data:
                    if isinstance(v, str) and v.lower().endswith(".pdf"):
                        return v

        # Try DOM anchors
        links = await page.query_selector_all("a[href$='.pdf'], a[href*='download']")
        for a in links:
            href = await a.get_attribute("href")
            if href:
                if not href.startswith("http"):
                    href = self.BASE + href
                return href

        return None

    async def enrich_with_downloads(self, page: Page, items: List[RasListingItem], max_items: int = 25) -> List[RasListingItem]:
        out: List[RasListingItem] = []
        for it in items[:max_items]:
            if it.detail_url and not it.download_url:
                try:
                    it.download_url = await self.resolve_download_url(page, it.detail_url)
                except Exception:
                    pass
            out.append(it)
        return out
