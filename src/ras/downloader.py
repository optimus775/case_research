# src/ras/downloader.py

from __future__ import annotations
import asyncio
import os
import logging
from io import BytesIO
from typing import Optional
import re
from pathlib import Path
import httpx
from pdfminer.high_level import extract_text as pdf_extract_text
from .models import RasListingItem, RasRawDoc
from .net import async_retryable


class RasDownloader:
    def __init__(self, user_agent: str | None = None, cookies_header: str | None = None, page=None):
        self.user_agent = user_agent
        self.cookies_header = cookies_header
        self.http_proxy = os.getenv("RAS_HTTP_PROXY") or os.getenv("RAS_PROXY")
        self.page = page
        logging.getLogger(__name__).debug(
            "RasDownloader initialized (proxy=%s, ua=%s, has_cookies=%s)",
            bool(self.http_proxy), bool(self.user_agent), bool(self.cookies_header),
        )
        # Saving options
        self.save_pdfs = (os.getenv("RAS_SAVE_PDFS", "false").lower() in ("1", "true", "yes"))
        self.save_dir = os.getenv("RAS_SAVE_DIR") or "downloads/ras"

    def _headers(self, referer: Optional[str] = None) -> dict:
        h = {
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if self.user_agent:
            h["User-Agent"] = self.user_agent
        if self.cookies_header:
            h["Cookie"] = self.cookies_header
        if referer:
            h["Referer"] = referer
        return h

    @async_retryable(max_attempts=5)
    async def fetch_pdf(self, url: str, referer: Optional[str] = None, timeout_s: float = 90.0) -> bytes:
        logger = logging.getLogger(__name__)
        logger.debug("Fetching PDF: %s (referer=%s)", url, referer)
        # Prefer Playwright bound request if provided (shares cookies/session)
        if self.page is not None:
            resp = await self.page.request.get(url, headers=self._headers(referer))
            ct = resp.headers.get("content-type", "")
            content = await resp.body()
            if not resp.ok:
                raise Exception(f"Playwright fetch failed: {resp.status}")
            if ("application/pdf" not in ct) and not content.startswith(b"%PDF"):
                # Likely captcha/HTML
                raise Exception(f"Unexpected content ({ct}) while fetching PDF")
            logger.debug("Fetched PDF via Playwright: %d bytes from %s", len(content), url)
            return content
        # Fallback to httpx
        transport = httpx.AsyncHTTPTransport(proxy=self.http_proxy) if self.http_proxy else httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(
            timeout=timeout_s,
            http2=True,
            follow_redirects=True,
            transport=transport,
        ) as client:
            r = await client.get(url, headers=self._headers(referer))
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if ("application/pdf" not in ct) and not r.content.startswith(b"%PDF"):
                raise Exception(f"Unexpected content-type for PDF: {ct}")
            logger.debug("Fetched PDF bytes: %d from %s", len(r.content), url)
            return r.content

    async def extract_text(self, pdf_bytes: bytes) -> str:
        text = ""
        try:
            text = pdf_extract_text(BytesIO(pdf_bytes)) or ""
        except Exception as e:
            logging.getLogger(__name__).warning("PDF text extraction failed: %s", e)
            text = ""
        if len(text.strip()) < 300:
            # Optional OCR fallback (implement in ocr.py and import here)
            # from .ocr import ocr_pdf_bytes
            # text = await ocr_pdf_bytes(pdf_bytes, lang="rus")
            logging.getLogger(__name__).debug("Text short; OCR fallback skipped")
        return text

    async def fetch_and_parse(self, item: RasListingItem) -> RasRawDoc | None:
        logger = logging.getLogger(__name__)
        if not item.download_url and not item.detail_url:
            return None
        url = item.download_url
        if not url and item.detail_url:
            # Fallback for HtmlDocument route: append ?download=true
            if "/Ras/HtmlDocument/" in item.detail_url:
                base = item.detail_url
                if not base.startswith("http"):
                    base = "https://ras.arbitr.ru" + base
                url = base + ("&download=true" if "?" in base else "?download=true")
        if not url:
            return None
        pdf = await self.fetch_pdf(url, referer=item.detail_url)
        text = await self.extract_text(pdf)
        saved_path = None
        if self.save_pdfs:
            try:
                Path(self.save_dir).mkdir(parents=True, exist_ok=True)
                base_name = item.title or item.case_number or item.act_id or "document"
                safe = re.sub(r"[^\w\-. А-Яа-я_]+", "_", base_name).strip("._ ") or "document"
                if not safe.lower().endswith(".pdf"):
                    safe += ".pdf"
                out_path = Path(self.save_dir) / safe
                i = 1
                final_path = out_path
                while final_path.exists():
                    final_path = out_path.with_name(out_path.stem + f"_{i}" + out_path.suffix)
                    i += 1
                final_path.write_bytes(pdf)
                saved_path = str(final_path)
                logger.debug("Saved PDF to %s", saved_path)
            except Exception as e:
                logger.warning("Failed saving PDF: %s", e)
        logger.debug("Parsed PDF for case %s: %d bytes, text_len=%d", item.case_number, len(pdf), len(text))
        return RasRawDoc(
            listing_id=item.act_id or item.case_id,
            case_number=item.case_number,
            doc_type=item.doc_type,
            date=item.date,
            court=item.court,
            source_url=item.detail_url,
            filename=(Path(saved_path).name if saved_path else (item.title or "document.pdf")),
            bytes_len=len(pdf),
            text=text,
            meta={"download_url": url, "saved_path": saved_path} if saved_path else {"download_url": url},
        )
