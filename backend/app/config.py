from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _origins() -> tuple[str, ...]:
    raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173")
    origins = tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    if "*" in origins:
        raise ValueError("CORS wildcard origins are not allowed")
    return origins


@dataclass(frozen=True)
class Settings:
    upload_root: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "reference-check-agent"
    )
    session_ttl_seconds: int = 2 * 60 * 60
    original_ttl_seconds: int = 10 * 60
    sweep_interval_seconds: float = 30.0
    event_buffer_size: int = 1_000
    heartbeat_seconds: float = 9.0
    ip_hourly_limit: int = 10
    ip_concurrent_limit: int = 1
    session_hourly_limit: int = 120
    memo_max_length: int = 180
    multipart_overhead_bytes: int = 1024 * 1024
    allowed_origins: tuple[str, ...] = field(default_factory=_origins)
