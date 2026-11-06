"""PII redaction and an append-only JSONL audit log for every agent run."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Order matters: SSN (3-2-4) before phone (3-3-4) so the more specific pattern wins.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{2}-\d{7}\b"), "[EIN]"),
    (
        re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"),
        "[PHONE]",
    ),
]

_MAX_STR = 2000


def redact(text: str) -> str:
    for pattern, label in _PATTERNS:
        text = pattern.sub(label, text)
    return text


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings inside dicts/lists and truncate very long ones."""
    if isinstance(obj, str):
        cleaned = redact(obj)
        return (
            cleaned if len(cleaned) <= _MAX_STR else cleaned[:_MAX_STR] + "…[truncated]"
        )
    if isinstance(obj, dict):
        return {str(k): redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


class AuditLog:
    """One JSONL file per run. Every tool call and model response is recorded, redacted."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "audit.jsonl"

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            **redact_obj(fields),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
