from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import Settings
from app.observability.middleware import CorrelationIdMiddleware
from app.rules.registry import RULES
from app.security.body_limit import RequestBodyLimitMiddleware
from app.security.headers import SecurityHeadersMiddleware
from app.security.rate_limit import RateLimiter
from app.security.uploads import MAX_FILE_SIZE
from app.sessions.store import Clock, SessionStore, utc_now


def create_app(settings: Settings | None = None, clock: Clock = utc_now) -> FastAPI:
    effective_settings = settings or Settings()
    store = SessionStore(effective_settings, clock)
    limiter = RateLimiter(clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.startup_sweep()
        sweep_task = asyncio.create_task(_sweep_loop(store), name="session-sweep")
        try:
            yield
        finally:
            store.accepting_uploads = False
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
            await store.shutdown()

    application = FastAPI(
        title="Reference Check Agent",
        version="0.2.0",
        lifespan=lifespan,
    )
    application.state.settings = effective_settings
    application.state.session_store = store
    application.state.rate_limiter = limiter
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=MAX_FILE_SIZE + effective_settings.multipart_overhead_bytes,
    )
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(CorrelationIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(effective_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Correlation-ID"],
    )
    application.include_router(router)

    @application.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready() -> dict[str, object]:
        ready_state = len(RULES) == 12 and all(
            rule.rule_id == rule_id for rule_id, rule in RULES.items()
        )
        return {"status": "ready" if ready_state else "not-ready", "rules": len(RULES)}

    return application


async def _sweep_loop(store: SessionStore) -> None:
    while True:
        await asyncio.sleep(store.settings.sweep_interval_seconds)
        await store.sweep_expired()


app = create_app()
