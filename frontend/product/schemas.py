from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=200)


class ConversationCreate(BaseModel):
    knowledge_base_id: str


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    use_web_search: bool = False


class ProductModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CitationResponse(ProductModel):
    id: str
    index: int
    document_id: str | None
    display_name: str
    page_number: int | None
    chunk_index: int | None
    section_title: str | None
    section_path: list[str] | None
    char_start: int | None
    char_end: int | None
    bbox: list[float] | None
    location_id: str | None
    source_locator: str | None
    parser_version: str | None
    extraction_method: str | None
    source_type: str
    source_url: str | None
    source_domain: str | None
    trusted: bool
    text: str
    supported: bool


class MessageResponse(ProductModel):
    id: str
    role: str
    content: str
    status: str
    answer_state: str | None
    created_at: datetime
    citations: list[CitationResponse] = Field(default_factory=list)
