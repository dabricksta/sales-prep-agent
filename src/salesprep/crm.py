"""A tiny mock CRM on SQLite, plus a human-approval queue for agent-proposed writes.

Security design:
- The agent only ever sees `agent_view()`, which omits contact email and phone.
- The agent can never write to the CRM. It can only *propose* an update; a human
  approves or rejects it from the CLI, and only allowlisted columns can change.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

LEAD_COLUMNS = (
    "id",
    "company_name",
    "domain",
    "contact_name",
    "contact_email",
    "contact_phone",
    "industry",
    "employee_count",
    "state",
    "status",
    "notes",
    "fit_score",
    "enrichment_summary",
)

# Columns the agent may *propose* changes to. Notably excludes `status`.
WRITABLE_FIELDS = frozenset({"industry", "employee_count", "notes", "fit_score", "enrichment_summary"})

# Columns never shown to the agent.
AGENT_HIDDEN = frozenset({"contact_email", "contact_phone"})


class DisallowedFieldError(ValueError):
    def __init__(self, fields: list[str]) -> None:
        self.fields = sorted(fields)
        super().__init__(
            f"Field(s) {', '.join(self.fields)} cannot be proposed. Allowed fields: {', '.join(sorted(WRITABLE_FIELDS))}."
        )


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


class CRM:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with _connect(db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    contact_name TEXT,
                    contact_email TEXT,
                    contact_phone TEXT,
                    industry TEXT,
                    employee_count INTEGER,
                    state TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    notes TEXT,
                    fit_score INTEGER,
                    enrichment_summary TEXT
                )"""
            )

    def seed(self, leads: list[dict[str, Any]]) -> int:
        placeholders = ", ".join("?" for _ in LEAD_COLUMNS)
        with _connect(self.db_path) as conn:
            for lead in leads:
                conn.execute(
                    f"INSERT OR REPLACE INTO leads ({', '.join(LEAD_COLUMNS)}) VALUES ({placeholders})",
                    [lead.get(col) for col in LEAD_COLUMNS],
                )
        return len(leads)

    def get_lead(self, lead_id: str) -> dict[str, Any] | None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def agent_view(self, lead_id: str) -> dict[str, Any] | None:
        lead = self.get_lead(lead_id)
        if lead is None:
            return None
        return {k: v for k, v in lead.items() if k not in AGENT_HIDDEN}

    def apply_update(self, lead_id: str, fields: dict[str, Any]) -> None:
        """Apply an already-approved update. Column names are allowlisted, values parameterized."""
        bad = [k for k in fields if k not in WRITABLE_FIELDS]
        if bad:
            raise DisallowedFieldError(bad)
        if not fields:
            return
        assignments = ", ".join(f"{col} = ?" for col in fields)
        with _connect(self.db_path) as conn:
            conn.execute(f"UPDATE leads SET {assignments} WHERE id = ?", [*fields.values(), lead_id])


def _validate_fields(fields: Any) -> dict[str, Any]:
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")
    bad = [k for k in fields if k not in WRITABLE_FIELDS]
    if bad:
        raise DisallowedFieldError(bad)
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"employee_count", "fit_score"}:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
            if key == "fit_score" and value > 100:
                raise ValueError("fit_score must be between 0 and 100")
        else:
            if not isinstance(value, str) or len(value) > 1000:
                raise ValueError(f"{key} must be a string of at most 1000 characters")
        cleaned[key] = value
    return cleaned


class ApprovalQueue:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        with _connect(db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    run_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                )"""
            )

    def propose(self, lead_id: str, fields: Any, reason: str, run_id: str | None = None) -> int:
        cleaned = _validate_fields(fields)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        with _connect(self.db_path) as conn:
            if conn.execute("SELECT 1 FROM leads WHERE id = ?", (lead_id,)).fetchone() is None:
                raise ValueError(f"unknown lead {lead_id}")
            cur = conn.execute(
                "INSERT INTO approvals (lead_id, fields_json, reason, run_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    lead_id,
                    json.dumps(cleaned),
                    reason.strip()[:500],
                    run_id,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            return int(cur.lastrowid)

    def list(self, status: str | None = "pending") -> list[dict[str, Any]]:
        with _connect(self.db_path) as conn:
            if status is None:
                rows = conn.execute("SELECT * FROM approvals ORDER BY id").fetchall()
            else:
                rows = conn.execute("SELECT * FROM approvals WHERE status = ? ORDER BY id", (status,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["fields"] = json.loads(item.pop("fields_json"))
            out.append(item)
        return out

    def decide(self, approval_id: int, approve: bool, crm: CRM) -> dict[str, Any]:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise ValueError(f"approval {approval_id} not found")
            if row["status"] != "pending":
                raise ValueError(f"approval {approval_id} is already {row['status']}")
            new_status = "approved" if approve else "rejected"
            conn.execute(
                "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
                (new_status, datetime.now(timezone.utc).isoformat(timespec="seconds"), approval_id),
            )
            lead_id = row["lead_id"]
            fields = json.loads(row["fields_json"])
        if approve:
            crm.apply_update(lead_id, fields)
        return {"id": approval_id, "status": new_status, "lead_id": lead_id, "fields": fields}
