from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.rules.models import CheckResult


class CheckStatus(str, Enum):
    VALIDATING = "validating"
    EXTRACTING = "extracting"
    CHECKING = "checking"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Decision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    EXCLUDED = "excluded"


class ErrorInfo(BaseModel):
    code: str
    stage: str
    retryable: bool
    message: str


class CheckSession(BaseModel):
    id: UUID
    access_token_hash: str
    status: CheckStatus
    file_format: Literal["hwp", "hwpx"]
    file_size: int = Field(ge=0)
    page_count: Optional[int] = Field(default=None, ge=1, le=30)
    created_at: datetime
    expires_at: datetime
    current_stage: str
    error: Optional[ErrorInfo] = None


class SessionResult(CheckResult):
    decision: Decision = Decision.PENDING
    original_memo_text: str
