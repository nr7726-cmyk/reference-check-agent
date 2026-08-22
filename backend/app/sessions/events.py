from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque

EVENT_TYPES = {
    "stage_changed",
    "heartbeat",
    "result_added",
    "counts_changed",
    "completed",
    "failed",
    "ai_delta",
}


@dataclass(frozen=True)
class SessionEvent:
    id: int
    event: str
    data: dict[str, Any]
    created_at: datetime


class EventBus:
    def __init__(self, maxlen: int) -> None:
        self._events: Deque[SessionEvent] = deque(maxlen=maxlen)
        self._next_id = 1
        self._condition = asyncio.Condition()

    async def publish(
        self,
        event: str,
        data: dict[str, Any],
        created_at: datetime,
        *,
        retain: bool = True,
    ) -> SessionEvent:
        if event not in EVENT_TYPES:
            raise ValueError(f"unsupported event type: {event}")
        async with self._condition:
            item = SessionEvent(self._next_id, event, data, created_at)
            self._next_id += 1
            if retain:
                self._events.append(item)
            self._condition.notify_all()
            return item

    def after(self, event_id: int) -> list[SessionEvent]:
        return [event for event in self._events if event.id > event_id]

    def clear(self) -> None:
        self._events.clear()

    async def wait_after(self, event_id: int, timeout: float) -> list[SessionEvent]:
        existing = self.after(event_id)
        if existing:
            return existing
        async with self._condition:
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: bool(self.after(event_id))),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return []
        return self.after(event_id)
