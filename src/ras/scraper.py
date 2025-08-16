# src/ras/scraper.py

from __future__ import annotations
import asyncio
import json
import re
import logging
import os
from datetime import datetime
from urllib.parse import quote
from typing import Optional
import httpx
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
    SEL_TEXTAREA_TEXT = "textarea[placeholder*='текст документа']"
    SEL_CASE_INPUT = "input[placeholder*='например']"
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
        logging.getLogger(__name__).debug("Opening search page: %s", self.BASE)
        await page.goto(self.BASE, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(self.SEL_TEXTAREA_TEXT, timeout=30000)
        except Exception:
            # fallback to any textarea
            try:
                await page.wait_for_selector("textarea", timeout=15000)
            except Exception:
                pass

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
        logger = logging.getLogger(__name__)
        # Try to locate the search textbox/textarea robustly (prefer textarea)
        filled = False
        if q.text:
            try:
                el = await page.query_selector(self.SEL_TEXTAREA_TEXT)
                if el:
                    await el.fill(q.text)
                    filled = True
                    logger.debug("Filled textarea by placeholder")
            except Exception:
                pass
        if not filled and q.text:
            try:
                # Standard placeholder-based input fill (fallback)
                await self._safe_fill(page, self.SEL_INPUT_TEXT, q.text)
                filled = True
            except Exception:
                pass
        if not filled and q.text:
            try:
                # Try Playwright role-based getter
                tb = page.get_by_role("textbox").first
                await tb.fill(q.text)
                filled = True
                logger.debug("Filled by role-based textbox")
            except Exception:
                pass
        if not filled and q.text:
            # As last resort, enumerate inputs and fill the first visible
            try:
                for el in await page.query_selector_all("input"):
                    try:
                        vis = await el.is_visible()
                        disabled = await el.is_disabled()
                        t = (await el.get_attribute("type")) or "text"
                        if vis and not disabled and t in ("text", "search"):
                            await el.fill(q.text)
                            filled = True
                            logger.debug("Filled generic input field")
                            break
                    except Exception:
                        continue
            except Exception:
                pass
        # Dates
        await self._safe_fill(page, self.SEL_DATE_FROM, q.date_from)
        await self._safe_fill(page, self.SEL_DATE_TO, q.date_to)
        # Case number if provided
        await self._safe_fill(page, self.SEL_CASE_INPUT, q.case_number)
        # TODO: court_region / court_id / doc_types / instance —
        # implement with actual UI selectors (comboboxes/checkboxes), confirm via DevTools.
        # Try both clicking 'Найти' and pressing Enter
        clicked = False
        try:
            await self._safe_click(page, self.SEL_FIND_BTN)
            clicked = True
        except Exception:
            pass
        if not clicked and filled:
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
        logger.debug("Applied filters: text=%s, date_from=%s, date_to=%s", q.text, q.date_from, q.date_to)

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
                # plausible keys: Result.Items, results, documents, items
                # First try nested Result.Items as seen on ras.arbitr.ru
                if "Result" in data and isinstance(data["Result"], dict):
                    for k in ("Items", "items", "Rows", "rows"):
                        if k in data["Result"] and isinstance(data["Result"][k], list):
                            seq = data["Result"][k]
                            break
                # Flat alternatives
                if seq is None:
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
                act_id = row.get("ActId") or row.get("Id") or row.get("DocumentId")
                detail = row.get("DetailUrl") or row.get("Url") or row.get("Link")
                if not detail and act_id:
                    # Fallback to known HtmlDocument route
                    detail = f"{self.BASE}/Ras/HtmlDocument/{act_id}"
                # Derive direct PDF links when possible:
                # 1) /Document/Pdf/{CaseId}/{Id}/{FileName}?isAddStamp=True (preferred)
                # 2) /Kad/PdfDocument/{CaseId}/{Id}/{FileName}
                derived_pdf_doc = None
                derived_pdf_kad = None
                try:
                    case_guid = row.get("CaseId") or row.get("CaseID")
                    doc_guid = row.get("Id") or row.get("DocumentId") or row.get("ActId")
                    file_name = row.get("FileName") or row.get("Title")
                    if case_guid and doc_guid and file_name:
                        base_name = quote(file_name)
                        derived_pdf_doc = f"{self.BASE}/Document/Pdf/{case_guid}/{doc_guid}/{base_name}?isAddStamp=True"
                        derived_pdf_kad = f"{self.BASE}/Kad/PdfDocument/{case_guid}/{doc_guid}/{base_name}"
                except Exception:
                    derived_pdf_doc = None
                    derived_pdf_kad = None
                # Choose the best download URL:
                # - Prefer direct PDF links we derive (Document/Pdf or Kad/PdfDocument)
                # - Only trust row[DownloadUrl] if it clearly points to a PDF
                raw_dl = (row.get("DownloadUrl") or "") if isinstance(row.get("DownloadUrl"), str) else ""
                raw_dl_abs = None
                try:
                    if raw_dl and not raw_dl.startswith("http"):
                        raw_dl_abs = f"{self.BASE}{raw_dl}"
                    else:
                        raw_dl_abs = raw_dl or None
                except Exception:
                    raw_dl_abs = raw_dl or None
                looks_like_pdf = False
                if raw_dl_abs:
                    s = raw_dl_abs.lower()
                    looks_like_pdf = s.endswith(".pdf") or "/document/pdf/" in s or "/kad/pdfdocument/" in s
                chosen_download = None
                if derived_pdf_doc:
                    chosen_download = derived_pdf_doc
                elif derived_pdf_kad:
                    chosen_download = derived_pdf_kad
                elif looks_like_pdf:
                    chosen_download = raw_dl_abs

                items.append(RasListingItem(
                    act_id=act_id,
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
                    detail_url=detail,
                    download_url=chosen_download,
                    extra=row,
                ))
        return items

    async def collect_listings(self, page: Page, q: RasQuery, limit: int = 100) -> List[RasListingItem]:
        """Run a search, intercept XHR JSON, and fallback-parse DOM.
        Returns normalized listing items (metadata may be partial if fallback).
        """
        logger = logging.getLogger(__name__)
        # Optional deep network debug
        if os.getenv("RAS_DEBUG_NET", "").lower() in ("1", "true", "yes"):
            try:
                page.on("request", lambda req: logger.debug("REQ %s %s", req.method, req.url) if "arbitr.ru" in req.url else None)
                page.on("requestfailed", lambda req: logger.warning("REQ_FAILED %s %s: %s", req.method, req.url, req.failure) if "arbitr.ru" in req.url else None)
            except Exception:
                pass
        bucket = await self._intercept_json(page, self._predicate)
        await self.apply_filters(page, q)
        # Explicitly call site's search function to bypass UI quirks
        try:
            exists = await page.evaluate("() => typeof doSearchRequest === 'function'")
            logging.getLogger(__name__).debug("doSearchRequest present: %s", exists)
            if exists:
                await page.evaluate("() => doSearchRequest(1, false)")
        except Exception as e:
            logging.getLogger(__name__).warning("Calling doSearchRequest failed: %s", e)
        # Wait for results container or some network idle
        try:
            await page.wait_for_selector(self.SEL_RESULTS_CONTAINER, timeout=30000)
            # Wait until results container becomes visible if present
            try:
                await page.wait_for_function(
                    "sel => { const el = document.querySelector(sel); return el && !el.classList.contains('g-hidden'); }",
                    self.SEL_RESULTS_CONTAINER,
                    timeout=20000,
                )
            except Exception:
                pass
        except Exception:
            # proceed anyway, we might have JSON
            pass
        try:
            await page.wait_for_load_state("networkidle")
        except Exception:
            pass
        await asyncio.sleep(1.0)

        # Write snapshot for troubleshooting
        try:
            html = await page.content()
            with open("debug_ras_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            logger.debug("Saved page snapshot to debug_ras_page.html (len=%d)", len(html))
        except Exception:
            pass

        json_items = self._parse_list_json(bucket)
        logger.debug("XHR records captured: %d; parsed items: %d", len(bucket), len(json_items))
        items: List[RasListingItem] = []
        items.extend(json_items)

        if len(items) < 5:
            # Fallback to DOM parsing
            rows = await page.query_selector_all(self.SEL_RESULT_ROW)
            logger.debug("Fallback DOM rows found: %d", len(rows))
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
        # If still empty or too small, try API fallback with RecaptchaToken
        if len(out) < max(1, min(5, limit)):
            try:
                api_items = await self.api_fallback_search(page, q)
                for it in api_items:
                    key = (it.case_number or it.act_id or it.title, it.detail_url or it.title)
                    if key not in uniq:
                        uniq.add(key)
                        out.append(it)
                    if len(out) >= limit:
                        break
            except Exception as e:
                logger.warning("API fallback attempt failed: %s", e)

        logger.debug("Listings collected: %d (limit=%d)", len(out), limit)
        return out

    async def collect_listings_fast(self, page: Page, q: RasQuery, limit: int = 100, wait_after_ms: int = 3000) -> List[RasListingItem]:
        """Faster variant for stage-1: rely only on XHR JSON, skip DOM/API fallbacks.
        Minimizes waits so we can close the browser quickly after collecting links.
        """
        logger = logging.getLogger(__name__)
        bucket = await self._intercept_json(page, self._predicate)
        await self.apply_filters(page, q)
        try:
            exists = await page.evaluate("() => typeof doSearchRequest === 'function'")
            if exists:
                await page.evaluate("() => doSearchRequest(1, false)")
        except Exception:
            pass
        # Short, bounded wait for network/JSON to arrive
        try:
            await page.wait_for_load_state("networkidle", timeout=wait_after_ms)
        except Exception:
            pass
        await asyncio.sleep(min(2.0, wait_after_ms / 1000.0))
        items = self._parse_list_json(bucket)
        # Truncate
        out = []
        seen = set()
        for it in items:
            key = (it.case_number or it.act_id or it.title, it.detail_url or it.download_url)
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            if len(out) >= limit:
                break
        logger.debug("FAST listings collected: %d (limit=%d)", len(out), limit)
        return out

    @async_retryable(max_attempts=4)
    async def resolve_download_url(self, page: Page, detail_url: str) -> str | None:
        """Open detail page and try to discover a direct document download link.
        Strict PDF-only policy:
          1) Intercept JSON/XHR while the page loads; accept only direct .pdf or known PDF endpoints.
          2) Look for anchor tags that clearly point to PDF URLs only.
        """
        logger = logging.getLogger(__name__)
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
                        logger.debug("Resolved PDF via XHR for %s: %s", detail_url, v)
                        return v

        # Try DOM anchors (strict PDF-only selectors)
        links = await page.query_selector_all("a[href$='.pdf'], a[href*='/Document/Pdf/'], a[href*='/Kad/PdfDocument/']")
        for a in links:
            href = await a.get_attribute("href")
            if href:
                if not href.startswith("http"):
                    href = self.BASE + href
                logger.debug("Resolved PDF via DOM for %s: %s", detail_url, href)
                return href

        logger.debug("No download URL resolved for %s", detail_url)
        return None

    async def enrich_with_downloads(self, page: Page, items: List[RasListingItem], max_items: int = 50) -> List[RasListingItem]:
        out: List[RasListingItem] = []
        for it in items[:max_items]:
            if it.detail_url and not it.download_url:
                try:
                    it.download_url = await self.resolve_download_url(page, it.detail_url)
                except Exception:
                    pass
            out.append(it)
        return out

    # Fallback: call backend API directly with RecaptchaToken acquired in-page
    async def _cookies_header_from_page(self, page: Page, domain_filter: Optional[str] = None) -> str:
        cookies = await page.context.cookies()
        parts: list[str] = []
        for c in cookies:
            dom = (c.get("domain") or "")
            if domain_filter and (domain_filter not in dom):
                continue
            n, v = c.get("name"), c.get("value")
            if n and v:
                parts.append(f"{n}={v}")
        return "; ".join(parts)

    async def _build_payload_from_dom(self, page: Page, q: RasQuery) -> dict:
        # Prefer site’s own builder if available
        try:
            exists = await page.evaluate("() => typeof returnRequestInfo === 'function'")
            if exists:
                payload = await page.evaluate("() => returnRequestInfo(1, false)")
                if isinstance(payload, dict):
                    # Adjust paging/size based on our query
                    payload["Page"] = int(q.page or 1)
                    payload["Count"] = int(q.per_page or 25)
                    # Ensure text exists if requested
                    if q.text and (not payload.get("Text")):
                        payload["Text"] = q.text
                    return payload
        except Exception:
            pass
        # Manual payload as fallback
        def dt_from(s: Optional[str]) -> str:
            if not s:
                return "2000-01-01T00:00:00"
            try:
                return datetime.fromisoformat(s).strftime("%Y-%m-%dT00:00:00")
            except Exception:
                return "2000-01-01T00:00:00"

        def dt_to(s: Optional[str]) -> str:
            if not s:
                return "2030-01-01T23:59:59"
            try:
                return datetime.fromisoformat(s).strftime("%Y-%m-%dT23:59:59")
            except Exception:
                return "2030-01-01T23:59:59"

        payload = {
            "GroupByCase": False,
            "Count": int(q.per_page or 25),
            "Page": int(q.page or 1),
            "DateFrom": dt_from(q.date_from),
            "DateTo": dt_to(q.date_to),
            "Sides": [],
            "Judges": [],
            "Cases": [q.case_number] if q.case_number else [],
            "Text": q.text or "",
        }
        return payload

    async def api_fallback_search(self, page: Page, q: RasQuery) -> List[RasListingItem]:
        logger = logging.getLogger(__name__)
        logger.debug("API fallback: starting")
        ua = await page.evaluate("() => navigator.userAgent")
        cookies_hdr = await self._cookies_header_from_page(page, domain_filter="arbitr.ru")
        token: Optional[str] = None
        # Try to obtain Recaptcha token via site’s API
        try:
            exists = await page.evaluate("() => typeof Common !== 'undefined' && typeof Common.executePravocaptcha === 'function'")
        except Exception:
            exists = False
        if exists:
            try:
                await page.evaluate("() => { window.__prtoken=null; Common.executePravocaptcha(function(tok){ window.__prtoken = tok; }); }")
                await page.wait_for_function("() => window.__prtoken && window.__prtoken.length > 0", timeout=30000)
                token = await page.evaluate("() => window.__prtoken")
                logger.debug("API fallback: RecaptchaToken acquired (%d chars)", len(token or ""))
            except Exception as e:
                logger.warning("API fallback: token acquisition failed: %s", e)
        else:
            logger.debug("API fallback: Common.executePravocaptcha not found")

        payload = await self._build_payload_from_dom(page, q)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": ua,
            "Cookie": cookies_hdr,
            "Origin": self.BASE,
            "Referer": f"{self.BASE}/",
        }
        if token:
            headers["RecaptchaToken"] = token

        proxy = os.getenv("RAS_HTTP_PROXY") or os.getenv("RAS_PROXY")
        transport = httpx.AsyncHTTPTransport(proxy=proxy) if proxy else httpx.AsyncHTTPTransport()
        url = f"{self.BASE}/Ras/Search"
        try:
            async with httpx.AsyncClient(timeout=60, transport=transport, follow_redirects=True) as client:
                r = await client.post(url, headers=headers, content=json.dumps(payload))
                logger.debug("API fallback: status=%s len=%s", r.status_code, len(r.content))
                if r.status_code != 200:
                    return []
                try:
                    data = r.json()
                except Exception:
                    return []
                parsed = self._parse_list_json([{"url": url, "json": data}])
                logger.debug("API fallback: parsed items=%d", len(parsed))
                return parsed
        except Exception as e:
            logger.warning("API fallback failed: %s", e)
            return []
