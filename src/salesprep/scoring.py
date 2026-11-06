"""Deterministic fit scoring.

The agent supplies observed signals; this module does the arithmetic. Keeping the
score out of the model means it is reproducible and cannot be talked into a number.

The rubric is ILLUSTRATIVE. It is a stand-in for whatever a real sales team would
define, and the territory is configurable.
"""

from __future__ import annotations

import re
from typing import Any

PREFERRED_INDUSTRY_KEYWORDS = (
    "hospitality",
    "restaurant",
    "brew",
    "dental",
    "health",
    "medical",
    "construction",
    "landscap",
    "retail",
    "nonprofit",
    "manufactur",
)

_STATE_RE = re.compile(r"^[A-Za-z]{2}$")


def _employee_points(count: int | None) -> tuple[int, str]:
    if count is None:
        return 0, "employee count unknown"
    if count < 10:
        return 5, f"{count} employees: very small"
    if count < 50:
        return 15, f"{count} employees: small"
    if count < 250:
        return 25, f"{count} employees: sweet spot"
    if count < 1000:
        return 20, f"{count} employees: mid-size"
    return 10, f"{count} employees: enterprise-scale"


def score_fit(
    *,
    employee_count: int | None,
    states: list[str],
    tipped_or_seasonal_workforce: bool,
    hiring_hr_or_payroll_roles: bool,
    industry: str | None,
    home_region_states: tuple[str, ...] = ("MA",),
) -> dict[str, Any]:
    if employee_count is not None and (
        isinstance(employee_count, bool)
        or not isinstance(employee_count, int)
        or employee_count < 0
    ):
        raise ValueError("employee_count must be a non-negative integer")
    if not isinstance(states, list) or not all(
        isinstance(s, str) and _STATE_RE.match(s) for s in states
    ):
        raise ValueError(
            "states must be a list of two-letter US state codes, e.g. ['MA', 'NH']"
        )

    unique_states = sorted({s.upper() for s in states})
    home = {s.upper() for s in home_region_states}
    breakdown: list[dict[str, Any]] = []

    pts, note = _employee_points(employee_count)
    breakdown.append(
        {"factor": "company size", "points": pts, "max_points": 25, "note": note}
    )

    multi = len(unique_states) >= 2
    breakdown.append(
        {
            "factor": "multi-state payroll",
            "points": 20 if multi else 0,
            "max_points": 20,
            "note": f"operates in {', '.join(unique_states) or 'unknown states'}",
        }
    )
    breakdown.append(
        {
            "factor": "tipped or seasonal workforce",
            "points": 20 if tipped_or_seasonal_workforce else 0,
            "max_points": 20,
            "note": (
                "complex wage/tax handling"
                if tipped_or_seasonal_workforce
                else "not observed"
            ),
        }
    )
    breakdown.append(
        {
            "factor": "hiring HR/payroll roles",
            "points": 15 if hiring_hr_or_payroll_roles else 0,
            "max_points": 15,
            "note": (
                "active need or capacity gap"
                if hiring_hr_or_payroll_roles
                else "not observed"
            ),
        }
    )
    industry_match = bool(industry) and any(
        k in industry.lower() for k in PREFERRED_INDUSTRY_KEYWORDS
    )
    breakdown.append(
        {
            "factor": "target industry",
            "points": 10 if industry_match else 0,
            "max_points": 10,
            "note": industry or "unknown industry",
        }
    )
    territory = bool(home & set(unique_states))
    breakdown.append(
        {
            "factor": "territory match",
            "points": 10 if territory else 0,
            "max_points": 10,
            "note": f"home region: {', '.join(sorted(home))}",
        }
    )

    return {"score": sum(item["points"] for item in breakdown), "breakdown": breakdown}
