from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from copilot import define_tool
from copilot.tools import Tool, ToolInvocation

from app.agents.models import (
    AgentSessionMemory,
    CitationCandidatesParams,
    MemoTemplateParams,
    ParagraphWindowParams,
    ResultItemParams,
    RuleEvidenceParams,
)
from app.rules.registry import RULES


def build_session_tools(memory: AgentSessionMemory) -> dict[str, Tool]:
    async def paragraph_window(
        params: ParagraphWindowParams, _invocation: ToolInvocation
    ) -> str:
        return memory.paragraph_windows.get(params.location_id, "")

    async def citation_candidates(
        params: CitationCandidatesParams, _invocation: ToolInvocation
    ) -> str:
        result = memory.results.get(params.result_id)
        if result is None:
            return "{}"
        candidates = [
            {
                "reference_id": reference.id,
                "authors": [author.normalized for author in reference.authors],
                "year": reference.year,
                "title": (reference.title or "")[:160],
            }
            for reference in memory.manuscript.references[:20]
        ]
        return json.dumps(
            {"finding": result.finding, "candidates": candidates},
            ensure_ascii=False,
        )

    async def rule_evidence(params: RuleEvidenceParams, _invocation: ToolInvocation) -> str:
        rule = RULES[params.rule_id]
        return rule.source.model_dump_json(exclude={"source_url"})

    async def memo_template(params: MemoTemplateParams, _invocation: ToolInvocation) -> str:
        return RULES[params.rule_id].memo_template

    async def result_item(params: ResultItemParams, _invocation: ToolInvocation) -> str:
        result = memory.results.get(params.result_id)
        return "{}" if result is None else result.model_dump_json(exclude={"memo_text"})

    definitions: list[tuple[str, str, type[Any], Callable[..., Any]]] = [
        (
            "get_paragraph_window",
            "Return the bounded manuscript data window for one location.",
            ParagraphWindowParams,
            paragraph_window,
        ),
        (
            "get_citation_candidates",
            "Return bounded structured reference candidates for one ambiguous result.",
            CitationCandidatesParams,
            citation_candidates,
        ),
        (
            "get_rule_evidence",
            "Return version-pinned rule evidence for one registered rule.",
            RuleEvidenceParams,
            rule_evidence,
        ),
        (
            "get_memo_template",
            "Return the approved Korean memo template for one rule.",
            MemoTemplateParams,
            memo_template,
        ),
        (
            "get_result_item",
            "Return one validated result without its memo text.",
            ResultItemParams,
            result_item,
        ),
    ]
    return {
        name: define_tool(
            name,
            description=description,
            params_type=params_type,
            handler=handler,
            skip_permission=True,
            defer="never",
        )
        for name, description, params_type, handler in definitions
    }
