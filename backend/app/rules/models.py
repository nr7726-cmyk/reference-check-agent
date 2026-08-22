from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, Field, model_validator

from app.extraction.models import Location


class Category(str, Enum):
    MISSING = "누락"
    MISMATCH = "불일치"
    FORMAT = "형식수정"
    NEEDS_REVIEW = "확인 필요"
    NORMAL = "정상"


class Severity(str, Enum):
    ERROR = "오류"
    WARNING = "경고"
    NEEDS_REVIEW = "확인 필요"
    INFO = "정보"


class ResultStatus(str, Enum):
    DETECTED = "detected"
    NEEDS_CONTEXT = "needs_context"
    PASSED = "passed"


class RuleSource(BaseModel):
    document_name: str
    version_or_published_at: str
    clause_number: Optional[str] = None
    page: Optional[int] = None
    section_title: str = "{공식 위치}"
    source_url: Optional[str] = None
    verified_at: Optional[date] = None
    verified: bool = False

    @model_validator(mode="after")
    def verified_source_has_locator(self) -> RuleSource:
        if (
            self.verified
            and not self.clause_number
            and (self.page is None or self.section_title == "{공식 위치}")
        ):
            raise ValueError("verified source requires a clause number or stable page and section")
        return self


class RuleDefinition(BaseModel):
    rule_id: str = Field(pattern=r"^CR-(0[1-9]|1[0-2])$")
    category: Category
    severity: Severity
    source: RuleSource
    memo_template: str
    deterministic: bool

    def effective_severity(self) -> Severity:
        if not self.source.verified:
            return Severity.NEEDS_REVIEW
        return self.severity


class CheckResult(BaseModel):
    id: str
    category: Category
    severity: Severity
    status: ResultStatus
    location: Location
    finding: str
    memo_text: str
    rule_id: str
    rule_source: RuleSource
    confidence: float = Field(ge=0, le=1)
    ai_assisted: bool = False
    sort_key: Tuple[int, int, int, int]

    @model_validator(mode="after")
    def unverified_rules_cannot_be_errors(self) -> CheckResult:
        if not self.rule_source.verified and self.severity != Severity.NEEDS_REVIEW:
            raise ValueError("unverified rules can only produce needs-review severity")
        return self
