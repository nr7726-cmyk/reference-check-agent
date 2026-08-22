from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Optional, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from app.api.schemas import CheckCreated, CheckSummary, ResultPatch
from app.config import Settings
from app.extraction.errors import CorruptDocumentError, SecurityLimitError, UnsupportedDocumentError
from app.observability.logging import correlation_id_var
from app.security.rate_limit import RateLimiter, RateLimitExceeded
from app.security.uploads import MAX_FILE_SIZE, new_upload_directory, validate_upload_path
from app.sessions.models import CheckStatus, Decision, SessionResult
from app.sessions.store import TERMINAL_STATUSES, SessionState, SessionStore
from app.workflows.pipeline import DeterministicPipeline

router = APIRouter()
CHUNK_SIZE = 64 * 1024


def _store(request: Request) -> SessionStore:
    return cast(SessionStore, request.app.state.session_store)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _limiter(request: Request) -> RateLimiter:
    return cast(RateLimiter, request.app.state.rate_limiter)


async def authenticated_state(
    request: Request,
    session_id: UUID,
    authorization: Annotated[Optional[str], Header()] = None,
) -> AsyncIterator[SessionState]:
    store = _store(request)
    state = await store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="검사 세션을 찾을 수 없습니다")
    if state.session.expires_at <= store.clock():
        await store.expire(state)
    if state.session.status == CheckStatus.EXPIRED:
        raise HTTPException(status_code=410, detail="검사 세션이 만료되었습니다")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="세션 토큰이 필요합니다")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        authenticated = await store.authenticate(session_id, token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="세션 토큰이 올바르지 않습니다") from exc
    if authenticated is None:
        raise HTTPException(status_code=404, detail="검사 세션을 찾을 수 없습니다")
    try:
        release = await _limiter(request).acquire(
            f"session:{session_id}",
            _settings(request).session_hourly_limit,
            concurrent_limit=10,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="세션 요청 한도를 초과했습니다") from exc
    try:
        yield authenticated
    finally:
        release()


@router.post("/api/v1/checks", response_model=CheckCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_check(
    request: Request,
    files: Annotated[list[UploadFile], File()],
) -> CheckCreated:
    store = _store(request)
    settings = _settings(request)
    if not store.accepting_uploads:
        raise HTTPException(status_code=503, detail="서비스가 종료 중입니다")
    if len(files) != 1:
        raise HTTPException(status_code=422, detail="HWP 또는 HWPX 파일 1개만 업로드할 수 있습니다")

    ip = request.client.host if request.client else "unknown"
    try:
        release = await _limiter(request).acquire(
            f"ip:{ip}", settings.ip_hourly_limit, settings.ip_concurrent_limit
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail="업로드 요청 한도를 초과했습니다") from exc

    upload = files[0]
    directory = new_upload_directory(settings.upload_root)
    suffix = Path(upload.filename or "").suffix.lower().lstrip(".") or "upload"
    path = directory / f"{uuid.uuid4().hex}.{suffix}"
    size = 0
    transferred = False
    try:
        with path.open("xb") as target:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="파일 크기는 30MB 이하여야 합니다")
                target.write(chunk)
        try:
            validated = await asyncio.to_thread(
                validate_upload_path,
                path,
                upload.filename or "",
                upload.content_type,
            )
        except SecurityLimitError as exc:
            if "30MB" in str(exc):
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (UnsupportedDocumentError, CorruptDocumentError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        state, access_token = await store.create(
            validated.format,
            validated.size,
            correlation_id_var.get(),
            path,
        )
        pipeline = DeterministicPipeline(store, release)
        state.task = asyncio.create_task(pipeline.run(state), name=f"check-{state.session.id}")
        transferred = True
        return CheckCreated(
            id=state.session.id,
            access_token=access_token,
            status=state.session.status,
            events_url=f"/api/v1/checks/{state.session.id}/events",
            expires_at=state.session.expires_at,
        )
    finally:
        await upload.close()
        if not transferred:
            try:
                if directory.exists():
                    _remove_upload_directory(directory)
            finally:
                release()


@router.get("/api/v1/checks/{session_id}", response_model=CheckSummary)
async def get_check(state: Annotated[SessionState, Depends(authenticated_state)]) -> CheckSummary:
    return CheckSummary(
        **state.session.model_dump(exclude={"access_token_hash"}),
        category_counts=_counts(state),
    )


@router.get("/api/v1/checks/{session_id}/events")
async def get_events(
    request: Request,
    state: Annotated[SessionState, Depends(authenticated_state)],
    last_event_id: Annotated[Optional[str], Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    try:
        cursor = int(last_event_id) if last_event_id else 0
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Last-Event-ID가 올바르지 않습니다") from exc
    if cursor < 0:
        raise HTTPException(status_code=422, detail="Last-Event-ID가 올바르지 않습니다")

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        settings = _settings(request)
        while True:
            if await request.is_disconnected():
                break
            events = await state.events.wait_after(cursor, settings.heartbeat_seconds)
            if not events:
                heartbeat = await state.events.publish(
                    "heartbeat",
                    {"at": _store(request).clock().isoformat()},
                    _store(request).clock(),
                    retain=False,
                )
                events = [heartbeat]
            for event in events:
                if event.id <= cursor:
                    continue
                cursor = event.id
                payload = json.dumps(event.data, ensure_ascii=False, default=str)
                yield f"id: {event.id}\nevent: {event.event}\ndata: {payload}\n\n"
            if state.session.status in TERMINAL_STATUSES and not state.events.after(cursor):
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/api/v1/checks/{session_id}/results")
async def get_results(
    state: Annotated[SessionState, Depends(authenticated_state)],
) -> list[SessionResult]:
    return sorted(state.results.values(), key=lambda result: result.sort_key)


@router.patch("/api/v1/checks/{session_id}/results/{result_id}")
async def patch_result(
    result_id: str,
    patch: ResultPatch,
    state: Annotated[SessionState, Depends(authenticated_state)],
) -> SessionResult:
    result = state.results.get(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="검사 결과를 찾을 수 없습니다")
    if patch.memo_text is not None:
        result.memo_text = patch.memo_text
        result.decision = Decision.EDITED
    if patch.decision is not None:
        result.decision = patch.decision
    return result


@router.post("/api/v1/checks/{session_id}/cancel")
async def cancel_check(
    request: Request,
    state: Annotated[SessionState, Depends(authenticated_state)],
) -> dict[str, str]:
    if state.session.status in TERMINAL_STATUSES:
        return {"status": state.session.status.value}
    state.session.status = CheckStatus.CANCELLED
    state.session.current_stage = "cancelled"
    if state.task and not state.task.done():
        state.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.task
    else:
        await _store(request).cleanup_original(state)
    return {"status": "cancelled"}


@router.get("/api/v1/checks/{session_id}/export")
async def export_results(
    state: Annotated[SessionState, Depends(authenticated_state)],
) -> Response:
    approved = sorted(
        (result for result in state.results.values() if result.decision == Decision.APPROVED),
        key=lambda result: result.sort_key,
    )
    content = "\n\n".join(f"{result.location.id}\n{result.memo_text}" for result in approved)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="correction-requests.txt"'},
    )


def _counts(state: SessionState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in state.results.values():
        counts[result.category.value] = counts.get(result.category.value, 0) + 1
    return counts


def _remove_upload_directory(directory: Path) -> None:
    for child in directory.iterdir():
        child.unlink()
    directory.rmdir()
