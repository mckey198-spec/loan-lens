"""Structured JSON logging (Architecture §13, TOOLS.md §2.2)."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

REDACTED = "[REDACTED]"

# Keys matching these substrings are never logged in clear text (Architecture §12).
_SENSITIVE_KEY_RE = re.compile(
    r"(password|token|secret|pan|raw_bytes)",
    re.IGNORECASE,
)

# Mask `key=value` / `key: value` fragments inside free-form messages.
_SENSITIVE_MESSAGE_RE = re.compile(
    r"(?i)\b((?:password|token|secret|pan|raw_bytes)\s*[=:]\s*)(\S+)",
)

# Correlation ids from Architecture §13; emitted at the top level when present.
CORRELATION_KEYS: tuple[str, ...] = (
    "request_id",
    "application_id",
    "processing_run_id",
    "assessment_id",
    "input_snapshot_id",
)

_RESERVED_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "exc_info",
        "exc_text",
        "thread",
        "threadName",
        "taskName",
        "asctime",
    }
)


def _is_sensitive_key(key: str) -> bool:
    return _SENSITIVE_KEY_RE.search(key) is not None


def _redact_message(message: str) -> str:
    return _SENSITIVE_MESSAGE_RE.sub(rf"\1{REDACTED}", message)


def _redact_value(key: str, value: Any) -> Any:
    """Mask sensitive keys and binary payloads; recurse into mappings."""
    if _is_sensitive_key(key) or isinstance(value, (bytes, bytearray)):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(key, item) for item in value)
    return value


class SensitiveDataFilter(logging.Filter):
    """Redact secrets, PAN-like keys, and raw bytes before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, (bytes, bytearray)):
            record.msg = REDACTED
        elif isinstance(record.msg, str):
            record.msg = _redact_message(record.msg)

        if record.args:
            if isinstance(record.args, Mapping):
                record.args = {
                    str(k): _redact_value(str(k), v) for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    REDACTED if isinstance(arg, (bytes, bytearray)) else arg
                    for arg in record.args
                )

        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_LOG_RECORD_ATTRS:
                continue
            setattr(record, key, _redact_value(key, value))
        return True


class JsonFormatter(logging.Formatter):
    """Format each log record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in CORRELATION_KEYS:
            if key in record.__dict__ and record.__dict__[key] is not None:
                payload[key] = record.__dict__[key]

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key in CORRELATION_KEYS:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info).rstrip()

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(log_level: str = "INFO") -> None:
    """Initialize the root logger for single-line JSON output with redaction."""
    level_name = log_level.upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
