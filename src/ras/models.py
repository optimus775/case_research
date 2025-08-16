# src/ras/models.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class RasQuery(BaseModel):
    """Query filters for ras.vectorp.ru search.
    NOTE: adapt field mapping to site filter controls/XHR payload.
    """
    text: Optional[str] = Field(default=None, description="Keywords/factual narrative")
    case_number: Optional[str] = None
    court_region: Optional[str] = None  # human name or site code; normalize in scraper
    court_id: Optional[str] = None      # exact court selector value (if known)
    doc_types: Optional[List[str]] = None  # ["Решение", "Определение", ...] → normalize
    instance: Optional[str] = None      # e.g. "первая", "апелляция", ... → normalize
    date_from: Optional[str] = None     # YYYY-MM-DD
    date_to: Optional[str] = None       # YYYY-MM-DD
    page: int = 1
    per_page: int = 50


class RasListingItem(BaseModel):
    """Single search result (listing row)."""
    act_id: Optional[str] = None
    case_id: Optional[str] = None
    case_number: Optional[str] = None
    instance: Optional[str] = None
    doc_type: Optional[str] = None
    court: Optional[str] = None
    court_code: Optional[str] = None
    region: Optional[str] = None
    date: Optional[str] = None
    title: Optional[str] = None
    parties: Optional[str] = None
    detail_url: Optional[str] = None
    download_url: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class RasRawDoc(BaseModel):
    """Downloaded document with extracted text."""
    listing_id: Optional[str] = None
    case_number: Optional[str] = None
    doc_type: Optional[str] = None
    date: Optional[str] = None
    court: Optional[str] = None
    source_url: Optional[str] = None
    filename: Optional[str] = None
    bytes_len: Optional[int] = None
    text: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
