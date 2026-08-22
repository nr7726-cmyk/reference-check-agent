from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

from agent_framework import WorkflowBuilder

from app.agents.models import AgentWorkflowState
from app.agents.roles import (
    CitationMatcherAgent,
    ExtractionCoordinatorAgent,
    MemoWriterAgent,
    ResultAggregatorAgent,
    RuleInterpretationAgent,
)
from app.agents.runtime import AIUsageLimiter, CopilotRuntime
from app.config import Settings
from app.extraction.models import ParsedManuscript
from app.rules.models import CheckResult

DeltaEmitter = Callable[[str], Awaitable[None]]
_LIMITERS: dict[tuple[int, int], AIUsageLimiter] = {}


async def enrich_with_agent_workflow(
    settings: Settings,
    manuscript: ParsedManuscript,
    results: list[CheckResult],
    emit_delta: DeltaEmitter,
    runtime: CopilotRuntime | None = None,
) -> list[CheckResult]:
    if not any(result.status.value == "needs_context" for result in results):
        return results
    limiter_key = (settings.ai_global_concurrency, settings.ai_calls_per_minute)
    limiter = _LIMITERS.setdefault(
        limiter_key,
        AIUsageLimiter(*limiter_key),
    )
    active_runtime = runtime or CopilotRuntime(settings, limiter)
    if not await active_runtime.start():
        return results
    snapshot = [result.model_copy(deep=True) for result in results]
    try:
        state = AgentWorkflowState(
            manuscript=manuscript,
            results=[result.model_copy(deep=True) for result in results],
        )
        extraction = ExtractionCoordinatorAgent("extract", active_runtime, emit_delta)
        citation = CitationMatcherAgent("citation", active_runtime, emit_delta)
        interpretation = RuleInterpretationAgent("interpret", active_runtime, emit_delta)
        memo = MemoWriterAgent("memo", active_runtime, emit_delta)
        aggregator = ResultAggregatorAgent("aggregate", active_runtime, emit_delta)
        workflow = WorkflowBuilder(
            start_executor=extraction,
            output_from=[aggregator],
        )
        workflow.add_edge(extraction, citation, condition=_has_citation_ambiguity)
        workflow.add_edge(
            extraction,
            interpretation,
            condition=_has_rule_ambiguity_without_citation,
        )
        workflow.add_edge(extraction, memo, condition=_has_no_analysis_candidates)
        workflow.add_edge(citation, interpretation, condition=_has_rule_ambiguity)
        workflow.add_edge(citation, memo, condition=_has_no_rule_ambiguity)
        workflow.add_edge(interpretation, memo)
        workflow.add_edge(memo, aggregator)
        built = workflow.build()
        run = cast(Awaitable[object], built.run(state))
        await asyncio.wait_for(run, timeout=settings.ai_total_timeout_seconds)
        return state.results
    except Exception:
        return snapshot
    finally:
        await active_runtime.close()


def build_agent_workflow_for_test(
    runtime: CopilotRuntime,
    emit_delta: DeltaEmitter,
) -> object:
    extraction = ExtractionCoordinatorAgent("extract", runtime, emit_delta)
    citation = CitationMatcherAgent("citation", runtime, emit_delta)
    interpretation = RuleInterpretationAgent("interpret", runtime, emit_delta)
    memo = MemoWriterAgent("memo", runtime, emit_delta)
    aggregator = ResultAggregatorAgent("aggregate", runtime, emit_delta)
    return (
        WorkflowBuilder(start_executor=extraction, output_from=[aggregator])
        .add_edge(extraction, citation, condition=_has_citation_ambiguity)
        .add_edge(citation, interpretation, condition=_has_rule_ambiguity)
        .add_edge(interpretation, memo)
        .add_edge(memo, aggregator)
        .build()
    )


def _has_citation_ambiguity(state: AgentWorkflowState) -> bool:
    return any(result.rule_id == "CR-03" for result in state.context_results)


def _has_rule_ambiguity(state: AgentWorkflowState) -> bool:
    return any(result.rule_id != "CR-03" for result in state.context_results)


def _has_rule_ambiguity_without_citation(state: AgentWorkflowState) -> bool:
    return not _has_citation_ambiguity(state) and _has_rule_ambiguity(state)


def _has_no_rule_ambiguity(state: AgentWorkflowState) -> bool:
    return not _has_rule_ambiguity(state)


def _has_no_analysis_candidates(state: AgentWorkflowState) -> bool:
    return not state.context_results
