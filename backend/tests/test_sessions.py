from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.sessions.events import EventBus
from app.sessions.models import CheckStatus
from app.sessions.store import SessionStore


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def test_event_bus_ids_and_reconnect_are_monotonic() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        bus = EventBus(maxlen=3)
        first = await bus.publish("stage_changed", {"stage": "one"}, clock())
        second = await bus.publish("heartbeat", {"at": "now"}, clock())
        third = await bus.publish("completed", {"summary": {}}, clock())
        assert [event.id for event in bus.after(first.id)] == [second.id, third.id]
        assert not await bus.wait_after(third.id, timeout=0.001)
        transient = await bus.publish("heartbeat", {"at": "later"}, clock(), retain=False)
        assert transient.id > third.id
        assert transient not in bus.after(third.id)
        bus.clear()
        assert bus.after(0) == []

    asyncio.run(scenario())


def test_secure_token_ttl_and_cleanup(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        settings = Settings(
            upload_root=tmp_path,
            session_ttl_seconds=7_200,
            original_ttl_seconds=600,
        )
        store = SessionStore(settings, clock)
        upload = tmp_path / "session" / "upload.hwpx"
        upload.parent.mkdir()
        upload.write_bytes(b"synthetic")
        state, token = await store.create("hwpx", upload.stat().st_size, "cid", upload)
        assert token not in state.session.access_token_hash
        assert len(token) >= 22
        assert await store.authenticate(state.session.id, token) is state

        clock.advance(601)
        old_timestamp = clock().timestamp() - 601
        os.utime(upload, (old_timestamp, old_timestamp))
        await store.sweep_expired()
        assert not upload.exists()
        assert not upload.parent.exists()

        clock.advance(6_600)
        await store.sweep_expired()
        assert state.session.status == CheckStatus.EXPIRED

    asyncio.run(scenario())


def test_startup_sweep_removes_abandoned_directories(tmp_path: Path) -> None:
    async def scenario() -> None:
        abandoned = tmp_path / "abandoned"
        abandoned.mkdir()
        (abandoned / "upload.hwpx").write_bytes(b"synthetic")
        store = SessionStore(Settings(upload_root=tmp_path))
        await store.startup_sweep()
        assert not abandoned.exists()

    asyncio.run(scenario())
