from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from app.config import Settings
from app.observability.logging import log_event
from app.sessions.events import EventBus
from app.sessions.models import CheckSession, CheckStatus, SessionResult

Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {
    CheckStatus.COMPLETED,
    CheckStatus.FAILED,
    CheckStatus.CANCELLED,
    CheckStatus.EXPIRED,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionState:
    session: CheckSession
    events: EventBus
    correlation_id: str
    results: dict[str, SessionResult] = field(default_factory=dict)
    task: Optional[asyncio.Task[None]] = None
    temp_path: Optional[Path] = None


class SessionStore:
    def __init__(self, settings: Settings, clock: Clock = utc_now) -> None:
        self.settings = settings
        self.clock = clock
        self._states: dict[UUID, SessionState] = {}
        self._lock = asyncio.Lock()
        self.accepting_uploads = True

    async def create(
        self,
        file_format: str,
        file_size: int,
        correlation_id: str,
        temp_path: Path,
    ) -> tuple[SessionState, str]:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        session = CheckSession(
            id=uuid4(),
            access_token_hash=_hash_token(token),
            status=CheckStatus.VALIDATING,
            file_format=file_format,
            file_size=file_size,
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.session_ttl_seconds),
            current_stage="validating",
        )
        state = SessionState(
            session=session,
            events=EventBus(self.settings.event_buffer_size),
            correlation_id=correlation_id,
            temp_path=temp_path,
        )
        async with self._lock:
            self._states[session.id] = state
        return state, token

    async def get(self, session_id: UUID) -> Optional[SessionState]:
        async with self._lock:
            return self._states.get(session_id)

    async def authenticate(self, session_id: UUID, token: str) -> Optional[SessionState]:
        state = await self.get(session_id)
        if state is None:
            return None
        if not secrets.compare_digest(state.session.access_token_hash, _hash_token(token)):
            raise PermissionError("invalid session token")
        return state

    async def sweep_expired(self) -> int:
        now = self.clock()
        expired = 0
        async with self._lock:
            states = list(self._states.values())
        for state in states:
            try:
                if state.session.expires_at <= now and state.session.status != CheckStatus.EXPIRED:
                    await self.expire(state)
                    expired += 1
                elif (
                    state.temp_path
                    and _age_seconds(state.temp_path, now) > self.settings.original_ttl_seconds
                ):
                    await self.cleanup_original(state)
            except OSError:
                log_event(
                    logger,
                    "session_cleanup_failed",
                    correlation_id=state.correlation_id,
                    stage="cleanup",
                    error_code="TEMP_CLEANUP_FAILED",
                )
        return expired

    async def expire(self, state: SessionState) -> None:
        if state.task and not state.task.done():
            state.task.cancel()
            if state.task is not asyncio.current_task():
                with contextlib.suppress(asyncio.CancelledError):
                    await state.task
        state.session.status = CheckStatus.EXPIRED
        state.session.current_stage = "expired"
        state.results.clear()
        state.events.clear()
        await self.cleanup_original(state)

    async def cleanup_original(self, state: SessionState) -> None:
        if state.temp_path:
            path = state.temp_path
            _delete_path(path)
            parent = path.parent
            if parent != self.settings.upload_root and parent.exists():
                with contextlib.suppress(OSError):
                    parent.rmdir()
            state.temp_path = None

    async def shutdown(self) -> None:
        self.accepting_uploads = False
        async with self._lock:
            states = list(self._states.values())
        tasks = [state.task for state in states if state.task and not state.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for state in states:
            await self.cleanup_original(state)

    async def startup_sweep(self) -> None:
        root = self.settings.upload_root
        if not root.exists():
            return
        for child in root.iterdir():
            _delete_path(child)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _age_seconds(path: Path, now: datetime) -> float:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (now - modified).total_seconds()


def _delete_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
    elif path.exists():
        path.unlink()
