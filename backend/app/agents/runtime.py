from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from copilot import CopilotClient, RuntimeConnection
from copilot.generated.rpc import PermissionDecisionDeniedInteractivelyByUser
from copilot.session import CopilotSession
from copilot.session_events import (
    AssistantMessageData,
    AssistantMessageDeltaData,
)
from copilot.tools import Tool

from app.agents.diagnostics import AI_DIAGNOSTICS
from app.agents.models import AIResultPatchList
from app.config import Settings

DeltaEmitter = Callable[[str], Awaitable[None]]


def deny_all_permissions(
    _request: object, _invocation: object
) -> PermissionDecisionDeniedInteractivelyByUser:
    return PermissionDecisionDeniedInteractivelyByUser()


class AIUsageLimiter:
    def __init__(self, concurrency: int, calls_per_minute: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._calls_per_minute = calls_per_minute
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] >= 60:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._calls_per_minute:
                AI_DIAGNOSTICS.quota_reached()
                return False
            if self._semaphore.locked():
                AI_DIAGNOSTICS.quota_reached()
                return False
            await self._semaphore.acquire()
            self._timestamps.append(now)
            return True

    def release(self) -> None:
        self._semaphore.release()


class CopilotRuntime:
    def __init__(self, settings: Settings, limiter: AIUsageLimiter) -> None:
        self.settings = settings
        self.limiter = limiter
        self._client: CopilotClient | None = None
        self._sessions: dict[str, CopilotSession] = {}
        self._quota_acquired = False

    @property
    def available(self) -> bool:
        return self.settings.ai_enabled

    async def start(self) -> bool:
        if not self.available or not await self.limiter.try_acquire():
            return False
        self._quota_acquired = True
        try:
            cli_path = self._prepare_cli_path()
            base_directory = self.settings.copilot_base_directory
            base_directory.mkdir(parents=True, exist_ok=True)
            connection = RuntimeConnection.for_stdio(path=str(cli_path))
            runtime_env = {
                "COPILOT_SKIP_CLI_DOWNLOAD": "1",
                "COPILOT_HOME": str(base_directory),
            }
            self._client = CopilotClient(
                connection=connection,
                env=runtime_env,
                github_token=self.settings.copilot_github_token,
                base_directory=str(base_directory),
                use_logged_in_user=False,
                session_idle_timeout_seconds=60,
            )
            await asyncio.wait_for(
                self._client.start(),
                timeout=self.settings.ai_start_timeout_seconds,
            )
            return True
        except Exception:
            AI_DIAGNOSTICS.fallback()
            await self.close()
            return False

    async def complete(
        self,
        role: str,
        instructions: str,
        prompt: str,
        tools: Sequence[Tool],
        emit_delta: DeltaEmitter,
    ) -> AIResultPatchList | None:
        if self._client is None:
            return None
        try:
            session = self._sessions.get(role)
            if session is None:
                session = await self._client.create_session(
                    model=self.settings.copilot_model,
                    session_id=f"reference-check-{role}-{os.urandom(6).hex()}",
                    tools=list(tools),
                    streaming=True,
                    enable_session_store=False,
                    available_tools=[],
                    excluded_tools=[
                        "shell",
                        "read_file",
                        "edit_file",
                        "web_fetch",
                        "mcp",
                    ],
                    on_permission_request=deny_all_permissions,
                )
                self._sessions[role] = session
            final_content = ""
            pending: set[asyncio.Future[None]] = set()

            def on_event(event: Any) -> None:
                nonlocal final_content
                if isinstance(event.data, AssistantMessageDeltaData):
                    delta = event.data.delta_content or ""
                    if delta:
                        task = asyncio.ensure_future(emit_delta(delta))
                        pending.add(task)
                        task.add_done_callback(pending.discard)
                elif isinstance(event.data, AssistantMessageData):
                    final_content = event.data.content

            unsubscribe = session.on(on_event)
            try:
                await session.send_and_wait(
                    f"{instructions}\n\n{prompt}",
                    timeout=self.settings.ai_total_timeout_seconds,
                )
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            finally:
                unsubscribe()
            return _parse_patch_list(final_content)
        except Exception:
            AI_DIAGNOSTICS.fallback()
            return None

    async def close(self) -> None:
        for session in self._sessions.values():
            with contextlib.suppress(Exception):
                await session.disconnect()
        self._sessions.clear()
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.stop()
            self._client = None
        if self._quota_acquired:
            self.limiter.release()
            self._quota_acquired = False

    def _prepare_cli_path(self) -> Path:
        path = self.settings.copilot_cli_path
        if path is None or not path.is_file():
            raise FileNotFoundError("Copilot CLI runtime is not configured")
        if os.name != "nt":
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return path


def _parse_patch_list(content: str) -> AIResultPatchList | None:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    try:
        return AIResultPatchList.model_validate(json.loads(cleaned))
    except (ValueError, json.JSONDecodeError):
        return None
