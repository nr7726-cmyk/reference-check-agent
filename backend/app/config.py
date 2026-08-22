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


def _optional_bool(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


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
    enable_ai_layer: bool | None = field(
        default_factory=lambda: (
            _optional_bool("AI_ENABLED")
            if os.getenv("AI_ENABLED") is not None
            else _optional_bool("ENABLE_AI_LAYER")
        )
    )
    copilot_github_token: str | None = field(
        default_factory=lambda: os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    )
    copilot_cli_path: Path | None = field(
        default_factory=lambda: (
            Path(value) if (value := os.getenv("COPILOT_CLI_PATH")) else None
        )
    )
    copilot_base_directory: Path = field(
        default_factory=lambda: Path(os.getenv("COPILOT_HOME", "/home/copilot"))
    )
    copilot_model: str = field(default_factory=lambda: os.getenv("COPILOT_MODEL", "gpt-5"))
    ai_start_timeout_seconds: float = field(
        default_factory=lambda: _env_float("AI_START_TIMEOUT_SECONDS", 8.0)
    )
    ai_total_timeout_seconds: float = field(
        default_factory=lambda: _env_float("AI_TOTAL_TIMEOUT_SECONDS", 45.0)
    )
    ai_session_call_limit: int = field(
        default_factory=lambda: _env_int("AI_SESSION_CALL_LIMIT", 4)
    )
    ai_global_concurrency: int = field(
        default_factory=lambda: _env_int("AI_GLOBAL_CONCURRENCY", 2)
    )
    ai_calls_per_minute: int = field(
        default_factory=lambda: _env_int("AI_CALLS_PER_MINUTE", 20)
    )
    allowed_origins: tuple[str, ...] = field(default_factory=_origins)

    @property
    def ai_enabled(self) -> bool:
        configured = (
            bool(self.copilot_github_token)
            and self.copilot_cli_path is not None
            and self.copilot_cli_path.is_file()
        )
        return configured if self.enable_ai_layer is None else self.enable_ai_layer and configured
