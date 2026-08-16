from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from deepsearcher.versioning import normalize_version_family


class AuthSetup(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=50)


class AuthLogin(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=50)
    role: Literal["admin", "member"] = "member"


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=200)


class HealthActionsRun(BaseModel):
    actions: list[str] = Field(min_length=1)


class ConversationCreate(BaseModel):
    knowledge_base_id: str


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    use_web_search: bool = False


class DocumentTemporalUpdate(BaseModel):
    published_at: date | None = None
    effective_at: date | None = None
    superseded_at: date | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self):
        if self.superseded_at is not None:
            if self.published_at is not None and self.superseded_at < self.published_at:
                raise ValueError("superseded_at must not precede published_at")
            if self.effective_at is not None and self.superseded_at < self.effective_at:
                raise ValueError("superseded_at must not precede effective_at")
        return self


class DocumentGovernanceUpdate(DocumentTemporalUpdate):
    version_family: str | None = Field(default=None, max_length=128)

    @field_validator("version_family", mode="before")
    @classmethod
    def normalize_family(cls, value):
        if value in (None, ""):
            return None
        normalized = normalize_version_family(value)
        if normalized is None:
            raise ValueError("version_family is invalid")
        return normalized


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
    published_at: date | None
    effective_at: date | None
    superseded_at: date | None
    temporal_metadata_source: str | None
    version_family: str | None
    version_family_source: str | None
    text: str
    supported: bool


class AnswerClaimResponse(ProductModel):
    id: str
    index: int
    text: str
    support_status: str
    structural_support_status: str = "unsupported"
    citation_indices: list[int] = Field(default_factory=list)
    citation_spans: list[dict] = Field(default_factory=list)
    citation_status: str = "missing"
    entailment_status: str = "not_checked"
    consistency_status: str = "not_checked"
    consistency_checks: list[dict] = Field(default_factory=list)
    risk_status: str = "not_assessed"
    risk_checks: list[dict] = Field(default_factory=list)
    confidence: float | None = None
    reason_codes: list[str] = Field(default_factory=list)


class MessageResponse(ProductModel):
    id: str
    role: str
    content: str
    status: str
    answer_state: str | None
    trust_contract_version: int | None = None
    trust_status: str | None = None
    safety_status: str | None = None
    policy_action: str | None = None
    policy_profile: str | None = None
    policy_reason_codes: list[str] | None = None
    risk_level: str | None = None
    query_type: str | None = None
    risk_factors: list[str] | None = None
    provenance_contract_version: int | None = None
    provenance_digest: str | None = None
    trust_details: dict | None = None
    created_at: datetime
    citations: list[CitationResponse] = Field(default_factory=list)
    claims: list[AnswerClaimResponse] = Field(default_factory=list)
