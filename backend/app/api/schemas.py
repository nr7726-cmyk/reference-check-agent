from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.sessions.models import CheckStatus, Decision, ErrorInfo


class CheckCreated(BaseModel):
    id: UUID
    access_token: str
    status: CheckStatus
    events_url: str
    expires_at: datetime


class CheckSummary(BaseModel):
    id: UUID
    status: CheckStatus
    file_format: str
    file_size: int
    page_count: Optional[int]
    created_at: datetime
    expires_at: datetime
    current_stage: str
    error: Optional[ErrorInfo]
    category_counts: dict[str, int]


class ResultPatch(BaseModel):
    decision: Optional[Decision] = None
    memo_text: Optional[str] = Field(default=None, min_length=1, max_length=180)

    @model_validator(mode="after")
    def has_change(self) -> ResultPatch:
        if self.decision is None and self.memo_text is None:
            raise ValueError("decision or memo_text is required")
        return self
