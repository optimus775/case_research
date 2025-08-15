# src/ras/models.py
from pydantic import BaseModel, Field
from typing import Optional, List

class RasQuery(BaseModel):
    text: Optional[str] = Field(None, description="Ключевые слова/фабула")
    case_number: Optional[str] = None
    court_region: Optional[str] = None   # ваш код/наименование региона суда
    court_id: Optional[str] = None       # прицельно по суду, если требуется
    doc_types: Optional[List[str]] = None
    date_from: Optional[str] = None      # YYYY-MM-DD
    date_to: Optional[str] = None

class RasListingItem(BaseModel):
    id: Optional[str]
    case_number: Optional[str]
    court: Optional[str]
    region: Optional[str]
    doc_type: Optional[str]
    date: Optional[str]
    title: Optional[str]
    parties: Optional[str]
    detail_url: Optional[str]
    download_url: Optional[str]

class RasRawDoc(BaseModel):
    listing_id: Optional[str]
    case_number: Optional[str]
    doc_type: Optional[str]
    date: Optional[str]
    court: Optional[str]
    source_url: Optional[str]
    filename: Optional[str]
    bytes_len: Optional[int]
    text: Optional[str]
