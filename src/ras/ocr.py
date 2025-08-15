# ─────────────────────────────────────────────────────────────────────────────
# File: ras/ocr.py (optional stub)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import asyncio
from typing import Optional

# Example stub for Tesseract OCR pipeline; wire up as needed.
# Requires: pytesseract, pillow, pdf2image or pypdfium2

async def ocr_pdf_bytes(pdf_bytes: bytes, lang: str = "rus") -> str:
    # TODO: implement OCR if your PDFs are frequently scanned images
    # Keep this async-friendly (run blocking OCR via asyncio.to_thread)
    return ""
    