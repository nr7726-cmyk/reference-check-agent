from __future__ import annotations

import json
import logging
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


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    safe = {key: value for key, value in fields.items() if key in ALLOWED_FIELDS}
    safe["correlation_id"] = safe.get("correlation_id") or correlation_id_var.get()
    logger.info(json.dumps({"event": event, **safe}, ensure_ascii=False, default=str))
