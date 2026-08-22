from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Optional, TypeVar, cast

from app.config import Settings
from app.extraction.citations import parse_manuscript
from app.extraction.errors import DocumentError
from app.extraction.hwp import extract_hwp
from app.extraction.hwpx import extract_hwpx
from app.extraction.models import ParsedManuscript
from app.observability.logging import log_event
from app.rules.engine import DeterministicRuleEngine
from app.rules.models import CheckResult
from app.sessions.models import CheckStatus, ErrorInfo, SessionResult
from app.sessions.store import SessionState, SessionStore

logger = logging.getLogger(__name__)
AiExtension = Callable[[SessionState], Awaitable[None]]
T = TypeVar("T")


class DeterministicPipeline:
    def __init__(
        self,
        store: SessionStore,
        release_rate_limit: Callable[[], None],
        ai_extension: Optional[AiExtension] = None,
    ) -> None:
        self.store = store
        self.release_rate_limit = release_rate_limit
        self.ai_extension = ai_extension

    async def run(self, state: SessionState) -> None:
        started = time.perf_counter()
        try:
            await self._stage(state, CheckStatus.VALIDATING, 10, "업로드 검증을 완료했습니다")
            await self._stage(state, CheckStatus.EXTRACTING, 25, "문서 구조를 추출하고 있습니다")
            if state.temp_path is None:
                raise RuntimeError("temporary upload is unavailable")
            source_path = state.temp_path
            if state.session.file_format == "hwpx":
                document = await _run_blocking(extract_hwpx, source_path)
            else:
                document = await _run_blocking(extract_hwp, source_path)
            state.session.page_count = document.page_count

            # The original is never needed after extraction.
            await self.store.cleanup_original(state)

            await self._stage(
                state, CheckStatus.CHECKING, 55, "인용과 참고문헌을 대조하고 있습니다"
            )
            manuscript = await _run_blocking(parse_manuscript, document)
            engine = DeterministicRuleEngine(
                self.store.settings.reference_order_summary_threshold,
                self.store.settings.citation_missing_summary_ratio,
                self.store.settings.citation_missing_summary_minimum,
                self.store.settings.review_repeat_summary_threshold,
            )
            results = await _run_blocking(engine.evaluate, manuscript)
            await self._stage(
                state,
                CheckStatus.REVIEWING,
                70,
                "문맥 확인 항목과 수정 요청 문구를 검토하고 있습니다",
            )
            results = await _enrich_results(self.store, state, manuscript, results)
            for result in results:
                stored = SessionResult(
                    **result.model_dump(),
                    original_memo_text=result.memo_text,
                )
                state.results[stored.id] = stored
                await state.events.publish(
                    "result_added",
                    {"result": stored.model_dump(mode="json")},
                    self.store.clock(),
                )
            await state.events.publish(
                "counts_changed",
                {"category_counts": _category_counts(state)},
                self.store.clock(),
            )

            if self.ai_extension is not None:
                await self.ai_extension(state)

            state.session.status = CheckStatus.COMPLETED
            state.session.current_stage = "completed"
            await state.events.publish(
                "completed",
                {
                    "summary": {"result_count": len(state.results)},
                    "expires_at": state.session.expires_at.isoformat(),
                },
                self.store.clock(),
            )
            log_event(
                logger,
                "pipeline_completed",
                correlation_id=state.correlation_id,
                stage="completed",
                duration_ms=round((time.perf_counter() - started) * 1000),
                file_format=state.session.file_format,
                page_count=state.session.page_count,
                result_count=len(state.results),
            )
        except asyncio.CancelledError:
            if state.session.status != CheckStatus.EXPIRED:
                state.session.status = CheckStatus.CANCELLED
                state.session.current_stage = "cancelled"
                await state.events.publish(
                    "stage_changed",
                    {"stage": "cancelled", "progress": 100, "message": "검사가 취소되었습니다"},
                    self.store.clock(),
                )
            raise
        except Exception as exc:
            await self._fail(state, exc)
        finally:
            try:
                await self.store.cleanup_original(state)
            finally:
                self.release_rate_limit()

    async def _stage(
        self, state: SessionState, status: CheckStatus, progress: int, message: str
    ) -> None:
        state.session.status = status
        state.session.current_stage = status.value
        await state.events.publish(
            "stage_changed",
            {"stage": status.value, "progress": progress, "message": message},
            self.store.clock(),
        )

    async def _fail(self, state: SessionState, exc: Exception) -> None:
        code = _error_code(exc)
        retryable = isinstance(exc, OSError)
        state.session.status = CheckStatus.FAILED
        state.session.error = ErrorInfo(
            code=code,
            stage=state.session.current_stage,
            retryable=retryable,
            message=_public_error_message(exc),
        )
        await state.events.publish(
            "failed",
            state.session.error.model_dump(mode="json"),
            self.store.clock(),
        )
        log_event(
            logger,
            "pipeline_failed",
            correlation_id=state.correlation_id,
            stage=state.session.current_stage,
            error_code=code,
            file_format=state.session.file_format,
        )


def _category_counts(state: SessionState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in state.results.values():
        key = result.category.value
        counts[key] = counts.get(key, 0) + 1
    return counts


async def _run_blocking(function: Callable[..., T], *args: object) -> T:
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await worker
        raise


def _error_code(exc: Exception) -> str:
    if isinstance(exc, DocumentError):
        return exc.__class__.__name__
    return "internal_error"


def _public_error_message(exc: Exception) -> str:
    if isinstance(exc, DocumentError):
        return str(exc)
    return "검사 중 내부 오류가 발생했습니다"


async def _enrich_results(
    store: SessionStore,
    state: SessionState,
    manuscript: ParsedManuscript,
    results: list[CheckResult],
) -> list[CheckResult]:
    if not store.settings.ai_enabled:
        return results
    enrich_with_agent_workflow = _load_agent_enricher()
    if enrich_with_agent_workflow is None:
        return results

    async def emit_delta(text: str) -> None:
        await state.events.publish(
            "ai_delta",
            {"text": text},
            store.clock(),
            retain=False,
        )

    try:
        return await enrich_with_agent_workflow(
            store.settings,
            manuscript,
            results,
            emit_delta,
        )
    except Exception:
        return results


AgentEnricher = Callable[
    [Settings, ParsedManuscript, list[CheckResult], Callable[[str], Awaitable[None]]],
    Awaitable[list[CheckResult]],
]


def _load_agent_enricher() -> AgentEnricher | None:
    try:
        module = importlib.import_module("app.workflows.agent_workflow")
    except ImportError:
        return None
    return cast(AgentEnricher, module.enrich_with_agent_workflow)
