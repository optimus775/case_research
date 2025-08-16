# src/ras/downloader.py

from __future__ import annotations
import asyncio
import os
import logging
import time
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
        # Disable proxy usage; do not read env for http proxy
        self.http_proxy = None
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
        # Не прокидываем сырые куки заголовком — используем CookieJar
        if referer:
            h["Referer"] = referer
        return h

    def _is_html_viewer(self, content: bytes) -> bool:
        """Check if content is an HTML viewer page pretending to be a PDF"""
        return (b"<html" in content[:500].lower() or
                b"<!doctype" in content[:500].lower() or
                b"<embed" in content or
                b"<iframe" in content)

    def _extract_embedded_url(self, content: bytes) -> Optional[str]:
        """Extract embedded PDF URL from viewer HTML"""
        # Look for various patterns of embedded PDFs
        patterns = [
            rb'embed\s+src="([^"]+)"',
            rb'iframe\s+src="([^"]+)"',
            rb'window\.location\.href\s*=\s*["\']([^"\']+\.pdf)',
            rb'document\.location\.replace\s*\(\s*["\']([^"\']+\.pdf)',
            rb'<a\s+href="([^"]+\.pdf)"[^>]*>'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                url = match.group(1).decode()
                # Handle base64 encoded PDFs
                if url.startswith("data:application/pdf;base64,"):
                    return None
                return url
        return None

    @async_retryable(max_attempts=3)
    async def fetch_pdf(self, url: str, referer: Optional[str] = None, timeout_s: float = 60.0) -> tuple[bytes, str]:
        logger = logging.getLogger(__name__)
        if not referer:
            referer = "https://ras.vectorp.ru/"
        logger.debug("Fetching PDF (strict): %s (referer=%s)", url, referer)
        u = (url or "").lower()
        if not (u.endswith(".pdf") or "/document/pdf/" in u or "/kad/pdfdocument/" in u):
            raise Exception(f"Non-PDF URL rejected by strict policy: {url}")

        if self.page is not None:
            try:
                content, final_url = await self._fetch_with_playwright(url)
                return content, final_url
            except Exception as e:
                logger.error("Playwright download failed, falling back to HTTPX: %s", e)

        return await self._fetch_with_httpx(url, referer, timeout_s)

    async def _fetch_with_playwright(self, url: str) -> tuple[bytes, str]:
        """Core Playwright download logic with multiple fallback strategies."""
        logger = logging.getLogger(__name__)
        save_path = self._get_save_path(url)
        logger.debug("DIAG: Starting PDF download automation for URL: %s", url)

        await self.page.goto(url, timeout=60000, wait_until="domcontentloaded")
        logger.debug("DIAG: Page navigation completed")
        await asyncio.sleep(2)

        page_info = await self._analyze_page()
        is_viewer = page_info.get('hasEmbed') or page_info.get('hasIframePdf') or page_info.get('chromeBuiltinViewer')
        logger.debug("DIAG: Detected viewer page: %s", is_viewer)

        if await self._try_download_from_viewer(save_path, is_viewer):
            pass
        elif await self._try_direct_download(save_path, url, is_viewer):
            pass
        elif await self._try_simulated_manual_download(save_path):
            pass
        else:
            raise Exception("All Playwright download approaches failed")

        with open(save_path, "rb") as f:
            content = f.read()
        logger.debug("DIAG: Successfully downloaded PDF: %d bytes", len(content))
        return content, self.page.url

    async def _analyze_page(self) -> dict:
        """Gathers diagnostic information about the current page."""
        logger = logging.getLogger(__name__)
        page_info = await self.page.evaluate("""() => {
            const info = {
                url: window.location.href,
                title: document.title,
                contentType: document.contentType || 'unknown',
                hasEmbed: document.querySelector('embed[type="application/pdf"]') !== null,
                hasIframePdf: document.querySelector('iframe[src*=".pdf"]') !== null,
                chromeBuiltinViewer: !!document.querySelector('embed[type="application/pdf"]')
            };
            return info;
        }""")
        logger.debug("DIAG: Page analysis complete: %s", page_info)
        return page_info

    async def _try_download_from_viewer(self, save_path: Path, is_viewer: bool) -> bool:
        """Attempts to download from the PDF viewer using Ctrl+S."""
        if not is_viewer:
            return False
        logger = logging.getLogger(__name__)
        logger.debug("DIAG: Attempting Chrome PDF viewer download via Ctrl+S...")
        try:
            await self.page.focus('body')
            async with self.page.expect_download(timeout=15000) as download_info:
                await self.page.keyboard.press('Control+s')
            download = await download_info.value
            await download.save_as(save_path)
            logger.debug("DIAG: Ctrl+S download succeeded: %s", save_path)
            return True
        except Exception as e:
            logger.debug("DIAG: Ctrl+S download failed: %s", e)
            return False

    async def _try_direct_download(self, save_path: Path, url: str, is_viewer: bool) -> bool:
        """Attempts a direct download if not in a viewer."""
        if is_viewer:
            return False
        logger = logging.getLogger(__name__)
        logger.debug("DIAG: Attempting direct download approach...")
        try:
            async with self.page.expect_download(timeout=10000) as download_info:
                await self.page.evaluate(f"window.location.href = '{url}'")
            download = await download_info.value
            await download.save_as(save_path)
            logger.debug("DIAG: Direct download succeeded: %s", save_path)
            return True
        except Exception as e:
            logger.debug("DIAG: Direct download failed: %s", e)
            return False

    async def _try_simulated_manual_download(self, save_path: Path) -> bool:
        """Simulates clicking a download button."""
        logger = logging.getLogger(__name__)
        logger.debug("DIAG: Attempting simulated manual download...")
        selectors = [
            'cr-icon-button[iron-icon="cr:file-download"]',
            '[aria-label*="Download"]',
            '[title*="Download"]',
        ]
        for selector in selectors:
            try:
                element = self.page.locator(selector).first
                if await element.is_visible(timeout=1000):
                    logger.debug("DIAG: Found download element with selector: %s", selector)
                    async with self.page.expect_download(timeout=10000) as download_info:
                        await element.click()
                    download = await download_info.value
                    await download.save_as(save_path)
                    logger.debug("DIAG: Manual simulation succeeded: %s", save_path)
                    return True
            except Exception as e:
                logger.debug("DIAG: Selector %s failed: %s", selector, e)
        return False

    async def _fetch_with_httpx(self, url: str, referer: str, timeout_s: float) -> tuple[bytes, str]:
        """Fetches a PDF using HTTPX as a fallback."""
        logger = logging.getLogger(__name__)
        transport = httpx.AsyncHTTPTransport()
        cookies = httpx.Cookies()
        if self.cookies_header:
            for part in self.cookies_header.split(";"):
                if "=" in part:
                    name, value = part.split("=", 1)
                    cookies.set(name.strip(), value.strip(), domain=".vectorp.ru", path="/")

        async with httpx.AsyncClient(
            timeout=timeout_s, http2=True, follow_redirects=True, transport=transport, cookies=cookies
        ) as client:
            headers = self._headers(referer)
            r = await client.get(url, headers=headers)
            if "text/html" in r.headers.get("content-type", "").lower():
                logger.debug("Non-PDF response (likely ddos-guard). Repeating request.")
                await asyncio.sleep(1)
                r = await client.get(url, headers=headers)

            r.raise_for_status()
            content = r.content
            if b"%PDF" not in content[:1024]:
                raise Exception(f"Unexpected content-type for PDF: {r.headers.get('content-type')}")
            logger.debug("Fetched PDF bytes: %d from %s", len(content), url)
            return content, str(r.url)

    def _get_save_path(self, url: str) -> Path:
        """Determines the save path for a download."""
        downloads_dir = Path(self.save_dir)
        downloads_dir.mkdir(parents=True, exist_ok=True)
        file_name = self._generate_filename(url=url, fallback_prefix=f"document_{int(time.time())}")
        return downloads_dir / file_name

    async def extract_text(self, pdf_path: Path, url: str) -> str:
        """Extracts text from a PDF file, with OCR fallback."""
        logger = logging.getLogger(__name__)
        text = ""
        try:
            with open(pdf_path, "rb") as f:
                text = pdf_extract_text(BytesIO(f.read())) or ""
        except Exception as e:
            logger.warning("PDF text extraction for %s failed: %s", url, e)
            text = ""
        
        if len(text.strip()) < 300:
            logger.debug("Text short for %s; OCR fallback skipped", url)
        return text

    def _generate_filename(
        self,
        item: RasListingItem | None = None,
        url: str | None = None,
        suggested_filename: str | None = None,
        fallback_prefix: str = "document",
    ) -> str:
        """Generate a meaningful filename for the PDF download."""
        if suggested_filename and re.match(r"[\w\-. ]+", suggested_filename):
            base_name = suggested_filename
        elif item:
            base_name = item.title or item.case_number or item.act_id or fallback_prefix
        elif url:
            from urllib.parse import unquote, urlparse
            try:
                path = unquote(urlparse(url).path)
                base_name = path.split('/')[-1] if '/' in path else fallback_prefix
            except Exception:
                base_name = fallback_prefix
        else:
            base_name = fallback_prefix

        safe_name = re.sub(r"[^\w\-. А-Яа-я_]+", "_", base_name).strip("._ ") or fallback_prefix
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        return safe_name

    async def fetch_and_parse(self, item: RasListingItem) -> RasRawDoc | None:
        """Fetches, saves, and parses a PDF document for a given listing item."""
        logger = logging.getLogger(__name__)
        if not item.download_url:
            logger.debug("Skipping item without direct PDF URL (case=%s)", item.case_number)
            return None
        
        url = item.download_url
        try:
            pdf_bytes, final_url = await self.fetch_pdf(url, referer="https://ras.vectorp.ru/")
        except Exception as e:
            logger.error("Failed to download PDF for case %s from %s: %s", item.case_number, url, e)
            return None

        saved_path = self._get_save_path(url, item=item)
        try:
            with open(saved_path, "wb") as f:
                f.write(pdf_bytes)
            logger.debug("Saved PDF to %s", saved_path)
        except Exception as e:
            logger.warning("Failed to save PDF %s: %s", saved_path, e)
            # Decide if returning a doc without a saved file is acceptable
            return None

        text = await self.extract_text(saved_path, final_url)
        logger.debug("Parsed PDF for case %s: %d bytes, text_len=%d", item.case_number, len(pdf_bytes), len(text))
        
        return RasRawDoc(
            listing_id=item.act_id or item.case_id,
            case_number=item.case_number,
            doc_type=item.doc_type,
            date=item.date,
            court=item.court,
            source_url=item.detail_url,
            filename=saved_path.name,
            bytes_len=len(pdf_bytes),
            text=text,
            meta={"download_url": final_url, "saved_path": str(saved_path)},
        )
