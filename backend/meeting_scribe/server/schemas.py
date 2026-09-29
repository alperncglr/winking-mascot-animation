from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MeetingCreate(BaseModel):
    title: Optional[str] = None
    language: Literal['mixed', 'tr', 'en', 'de', 'fr'] = 'mixed'


class RecordSecondsRequest(BaseModel):
    seconds: int = Field(default=10, ge=1, le=3600)


class ImportWavRequest(BaseModel):
    source_path: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
