# src/ras/downloader.py
import httpx, asyncio
from .models import RasListingItem, RasRawDoc
from pdfminer.high_level import extract_text as pdf_extract_text
from io import BytesIO

class RasDownloader:
    async def fetch_pdf_and_text(self, item: RasListingItem) -> RasRawDoc | None:
        if not item.detail_url:
            return None
        # 1) При необходимости — открыть detail_url через Playwright,
        #    найти прямую ссылку на файл (download_url).
        #    Здесь предполагаем, что download_url уже известен.
        if not item.download_url:
            return None

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.get(item.download_url)
            resp.raise_for_status()
            pdf_bytes = resp.content

        # Быстрый хек: сначала попытка извлечь текст как из PDF,
        # если текста мало — помечаем как скан и отправляем на OCR.
        text = ""
        try:
            text = pdf_extract_text(BytesIO(pdf_bytes)) or ""
        except Exception:
            text = ""

        if len(text.strip()) < 300:
            # OCR-пайплайн (tesseract/paddleocr) — подключите при необходимости
            # text = await run_ocr(pdf_bytes, lang="rus")
            pass

        return RasRawDoc(
            listing_id=None,
            case_number=item.case_number,
            doc_type=item.doc_type,
            date=item.date,
            court=item.court,
            source_url=item.detail_url,
            filename=item.title or "document.pdf",
            bytes_len=len(pdf_bytes),
            text=text
        )
