"""pydantic v2 schemas for the fastapi surface."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    top_k: int = Field(6, ge=1, le=20)


class Citation(BaseModel):
    source_id: str
    page: int
    chunk_index: int


class RetrievedChunk(BaseModel):
    chunk_id: str
    source_id: str
    page: int
    chunk_index: int
    score: float


class AskResponse(BaseModel):
    request_id: str
    answer: str
    citations: list[Citation]
    retrieved: list[RetrievedChunk]
    guardrail_prompt: Literal["ALLOW", "DENY", "DISABLED"] = "ALLOW"
    guardrail_response: Literal["ALLOW", "DENY", "DISABLED"] = "ALLOW"
    redaction_n: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class IngestFileSpec(BaseModel):
    path: str
    source_id: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_org_ids: list[str] = Field(default_factory=list)
    allowed_patient_ids: list[str] = Field(default_factory=list)
    sensitivity: Literal["public", "internal", "restricted"] = "internal"


class IngestRequest(BaseModel):
    files: list[IngestFileSpec]


class IngestResponse(BaseModel):
    ingested: int
    chunks: int


class AuditRowOut(BaseModel):
    id: int
    request_id: str
    ts: datetime
    user_id: str
    org_id: str
    question_hash: str
    retrieved_chunk_ids: list[str]
    guardrail_prompt: str
    guardrail_response: str
    row_hash: str
    prev_hash: str


class AuditQueryResponse(BaseModel):
    rows: list[AuditRowOut]
    chain_ok: bool
    chain_broken_at: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    store_count: int
