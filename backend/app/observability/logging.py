from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
ALLOWED_FIELDS = {
    "correlation_id",
    "stage",
    "duration_ms",
    "file_format",
    "size_bucket",
    "page_count",
    "rule_id",
    "result_count",
    "error_code",
}
SECRET_PATTERN = re.compile(
    r"(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|Bearer\s+[A-Za-z0-9._~-]+)",
    re.IGNORECASE,
)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    safe = {
        key: _redact(value)
        for key, value in fields.items()
        if key in ALLOWED_FIELDS
    }
    safe["correlation_id"] = safe.get("correlation_id") or correlation_id_var.get()
    logger.info(json.dumps({"event": event, **safe}, ensure_ascii=False, default=str))


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return SECRET_PATTERN.sub("[REDACTED]", value)
    return value
