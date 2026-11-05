"""Runtime configuration. Everything has a safe default; env vars override."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    # Where mutable state lives (CRM db, run logs, approvals): <root>/var
    root: Path = REPO_ROOT
    # Where read-only synthetic data lives: seed leads and offline web fixtures
    data_dir: Path = REPO_ROOT / "data"

    model: str = "claude-sonnet-5-5"
    web_mode: str = "fixture"  # "fixture" | "live"

    # Guardrails: hard caps on a single agent run
    max_steps: int = 12  # model calls
    max_tool_calls: int = 30
    max_total_tokens: int = 150_000  # cumulative input + output across the run
    max_output_tokens: int = 4096  # per model call
    page_char_limit: int = 6000  # per fetched page returned to the model

    # Illustrative territory used by the fit rubric. Configurable, not a claim
    # about any real company's coverage.
    home_region_states: tuple[str, ...] = ("MA",)

    # Webhook replay window
    webhook_max_skew_seconds: int = 300

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @property
    def seed_path(self) -> Path:
        return self.data_dir / "seed_leads.json"

    @property
    def var_dir(self) -> Path:
        return self.root / "var"

    @property
    def db_path(self) -> Path:
        return self.var_dir / "crm.sqlite3"

    @property
    def runs_dir(self) -> Path:
        return self.var_dir / "runs"

    @classmethod
    def from_env(cls) -> "Config":
        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None or raw == "":
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc

        web_mode = os.environ.get("SALESPREP_WEB", "fixture").strip().lower()
        if web_mode not in {"fixture", "live"}:
            raise SystemExit("SALESPREP_WEB must be 'fixture' or 'live'")

        root = Path(os.environ.get("SALESPREP_HOME") or REPO_ROOT)
        base = cls()
        return cls(
            root=root,
            data_dir=base.data_dir,
            model=os.environ.get("SALESPREP_MODEL", base.model),
            web_mode=web_mode,
            max_steps=_int("SALESPREP_MAX_STEPS", base.max_steps),
            max_tool_calls=_int("SALESPREP_MAX_TOOL_CALLS", base.max_tool_calls),
            max_total_tokens=_int("SALESPREP_MAX_TOTAL_TOKENS", base.max_total_tokens),
        )
