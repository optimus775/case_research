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
        # По умолчанию используем корневой реферер, без HtmlDocument
        if not referer:
            referer = "https://ras.vectorp.ru/"
        logger.debug("Fetching PDF (strict): %s (referer=%s)", url, referer)
        # Strict PDF-only policy: accept only direct PDF endpoints
        u = (url or "").lower()
        if not (u.endswith(".pdf") or "/document/pdf/" in u or "/kad/pdfdocument/" in u):
            raise Exception(f"Non-PDF URL rejected by strict policy: {url}")
        
        # Use Playwright's download API when page is available
        if self.page is not None:
            try:
                # Ensure downloads directory exists
                downloads_dir = Path(self.save_dir)
                downloads_dir.mkdir(parents=True, exist_ok=True)
                
                # Generate unique filename
                file_name = f"document_{int(time.time())}.pdf"
                save_path = downloads_dir / file_name
                
                # Navigate to URL with timeout
                await self.page.goto(url, timeout=60000, wait_until="domcontentloaded")
                
                # Check if we're on a viewer page
                is_viewer = await self.page.evaluate("""() => {
                    return document.querySelector('embed[type="application/pdf"]') !== null ||
                        document.querySelector('iframe[src*=".pdf"]') !== null;
                }""")
                
                # Wait for download to start
                async with self.page.expect_download(timeout=30000) as download_info:
                    if is_viewer:
                        # For viewer pages, trigger download via context menu
                        await self.page.evaluate("""() => {
                            const embed = document.querySelector('embed[type="application/pdf"]');
                            if (embed) {
                                const rect = embed.getBoundingClientRect();
                                const event = new MouseEvent('contextmenu', {
                                    bubbles: true,
                                    clientX: rect.left + rect.width/2,
                                    clientY: rect.top + rect.height/2
                                });
                                embed.dispatchEvent(event);
                            }
                        }""")
                        # Wait for context menu to appear
                        await self.page.wait_for_selector('text="Save as"', timeout=5000)
                        await self.page.click('text="Save as"')
                    else:
                        # For direct PDFs, trigger via first button or link
                        download_btn = await self.page.query_selector('a[href*=".pdf"], button')
                        if download_btn:
                            await download_btn.click()
                
                download = await download_info.value
                
                # Save file
                await download.save_as(save_path)
                logger.debug("Saved PDF via Playwright download: %s", save_path)
                
                # Read saved file
                with open(save_path, "rb") as f:
                    content = f.read()
                
                return content, url
            except Exception as e:
                logger.error("Playwright download failed: %s", e)
                # Fall back to HTTPX method

        # Fallback to HTTPX method
        transport = httpx.AsyncHTTPTransport()
        # Prepare cookie jar and pre-seed cookies from Playwright context if provided
        cookies = httpx.Cookies()
        if self.cookies_header:
            try:
                for part in self.cookies_header.split(";"):
                    part = part.strip()
                    if not part or "=" not in part:
                        continue
                    name, value = part.split("=", 1)
                    # Scope cookies to vectorp.ru
                    cookies.set(name.strip(), value.strip(), domain=".vectorp.ru", path="/")
            except Exception:
                pass
        async with httpx.AsyncClient(
            timeout=timeout_s,
            http2=True,
            follow_redirects=True,
            transport=transport,
            cookies=cookies,
        ) as client:
            # First request may hit ddos-guard gate; allow one extra pass if HTML
            headers = self._headers(referer)
            r = await client.get(url, headers=headers)
            
            r.raise_for_status()
            ct = (r.headers.get("content-type", "") or "").lower()
            content = r.content
            
            if ("application/pdf" not in ct) and not content.startswith(b"%PDF"):
                # If server is ddos-guard and returned small HTML, retry once to pass the gate
                if ("ddos-guard" in (" ".join([k.decode().lower()+":"+v.decode().lower() for k,v in r.headers.raw])) or "text/html" in ct) and len(content) < 8192:
                    logger.debug("Non-PDF response (likely ddos-guard). Repeating request after cookies.")
                    r = await client.get(url, headers=headers)
                    r.raise_for_status()
                    ct = (r.headers.get("content-type", "") or "").lower()
                    content = r.content
            if ("application/pdf" not in ct) and not content.startswith(b"%PDF"):
                raise Exception(f"Unexpected content-type for PDF: {ct}")
            logger.debug("Fetched PDF bytes: %d from %s", len(content), url)
            
            return content, url

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
        # Strict: only use direct PDF links discovered earlier
        url = item.download_url
        if not url:
            logger.debug("Skipping item without direct PDF URL (case=%s)", item.case_number)
            return None
        pdf_bytes, final_url = await self.fetch_pdf(url, referer="https://ras.vectorp.ru/")
        text = await self.extract_text(pdf_bytes)
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
                final_path.write_bytes(pdf_bytes)
                saved_path = str(final_path)
                logger.debug("Saved PDF to %s", saved_path)
            except Exception as e:
                logger.warning("Failed saving PDF: %s", e)
        logger.debug("Parsed PDF for case %s: %d bytes, text_len=%d", item.case_number, len(pdf_bytes), len(text))
        return RasRawDoc(
            listing_id=item.act_id or item.case_id,
            case_number=item.case_number,
            doc_type=item.doc_type,
            date=item.date,
            court=item.court,
            source_url=item.detail_url,
            filename=(Path(saved_path).name if saved_path else (item.title or "document.pdf")),
            bytes_len=len(pdf_bytes),
            text=text,
            meta={"download_url": final_url, "saved_path": saved_path} if saved_path else {"download_url": final_url},
        )
