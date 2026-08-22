from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.extraction.models import ParsedManuscript
from app.rules.models import CheckResult


class ParagraphWindowParams(BaseModel):
    location_id: str = Field(min_length=1, max_length=120)


class CitationCandidatesParams(BaseModel):
    result_id: str = Field(min_length=1, max_length=180)


class RuleEvidenceParams(BaseModel):
    rule_id: str = Field(pattern=r"^CR-(0[1-9]|1[0-2])$")


class MemoTemplateParams(BaseModel):
    rule_id: str = Field(pattern=r"^CR-(0[1-9]|1[0-2])$")


class ResultItemParams(BaseModel):
    result_id: str = Field(min_length=1, max_length=180)


class AIResultPatch(BaseModel):
    result_id: str
    memo_text: str = Field(min_length=1, max_length=180)
    confidence: float = Field(ge=0, le=1)
    supported: bool = True


class AIResultPatchList(BaseModel):
    items: list[AIResultPatch] = Field(max_length=20)


@dataclass
class AgentWorkflowState:
    manuscript: ParsedManuscript
    results: list[CheckResult]
    memory: AgentSessionMemory | None = None
    ai_calls: int = 0

    @property
    def context_results(self) -> list[CheckResult]:
        return [result for result in self.results if result.status.value == "needs_context"]


@dataclass
class AgentSessionMemory:
    manuscript: ParsedManuscript
    results: dict[str, CheckResult]
    paragraph_windows: dict[str, str] = field(default_factory=dict)
