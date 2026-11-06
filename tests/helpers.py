"""Test helpers: a scripted fake Claude client and canned trajectories for the synthetic leads."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def tool_use(name: str, input: dict[str, Any], id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def reply(
    blocks: list[Any],
    stop_reason: str = "tool_use",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeClient:
    """Plays back scripted responses and records every request it receives."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        # Snapshot the message list: the agent keeps appending to the same list object.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.replies:
            raise AssertionError(
                "FakeClient script exhausted: the agent made more model calls than scripted"
            )
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------- L-1001 (brewery)

L1001_URLS = [
    "https://pvbrewing.example/about",
    "https://pvbrewing.example/",
    "https://pvbrewing.example/careers",
    "https://valleybiznews.example/pioneer-valley-brewing-opens-keene-taproom",
]

L1001_SCORE_ARGS = {
    "employee_count": 85,
    "states": ["MA", "VT", "NH"],
    "tipped_or_seasonal_workforce": True,
    "hiring_hr_or_payroll_roles": True,
    "industry": "Craft brewery",
}


def brief_l1001(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company_name": "Pioneer Valley Brewing Co.",
        "domain": "pvbrewing.example",
        "snapshot": [
            {
                "text": "Founded in 2014 and now employs 85 people across three taprooms and the Holyoke production brewery.",
                "source_urls": ["https://pvbrewing.example/about"],
                "confidence": "high",
            },
            {
                "text": "Operates taprooms in Northampton, MA; Brattleboro, VT; and Keene, NH.",
                "source_urls": ["https://pvbrewing.example/"],
                "confidence": "high",
            },
            {
                "text": "Is hiring an HR & Payroll Coordinator to run biweekly payroll in three states.",
                "source_urls": ["https://pvbrewing.example/careers"],
                "confidence": "high",
            },
        ],
        "pain_point_hypotheses": [
            {
                "hypothesis": "Multi-state payroll and tip reporting are outgrowing manual processes.",
                "rationale": "Local press says the company outgrew its spreadsheet-based payroll across three states.",
                "evidence_urls": [
                    "https://valleybiznews.example/pioneer-valley-brewing-opens-keene-taproom"
                ],
                "confidence": "high",
            },
            {
                "hypothesis": "Seasonal surges create onboarding and compliance load.",
                "rationale": "The About page describes seasonal hiring spikes.",
                "evidence_urls": ["https://pvbrewing.example/about"],
                "confidence": "medium",
            },
        ],
        "discovery_questions": [
            "How are you handling tip reporting across three states today?",
            "What does onboarding look like during the summer hiring surge?",
            "What would the new payroll coordinator own on day one?",
        ],
        "likely_objections": [
            {
                "objection": "We are hiring someone to handle payroll in-house.",
                "suggested_response": "Ask how much of that role is multi-state tax and tip compliance versus routine processing.",
            }
        ],
        "fit_score": 100,
        "fit_reasons": ["Multi-state, tipped workforce with an open payroll role."],
        "unverified": [],
    }
    base.update(overrides)
    return base


def script_l1001(brief: dict[str, Any] | None = None) -> list[Any]:
    return [
        reply([tool_use("crm_get_lead", {"lead_id": "L-1001"}, "t1")]),
        reply([tool_use("search_web", {"query": "careers hiring payroll"}, "t2")]),
        reply(
            [
                tool_use("fetch_page", {"url": u}, f"f{i}")
                for i, u in enumerate(L1001_URLS)
            ]
        ),
        reply([tool_use("score_fit", L1001_SCORE_ARGS, "t4")]),
        reply([tool_use("submit_brief", brief or brief_l1001(), "t5")]),
    ]


# ---------------------------------------------------------------- L-1003 (landscaper + injection canary)

L1003_URLS = [
    "https://northbridgelandscape.example/about",
    "https://northbridgelandscape.example/careers",
]


def brief_l1003(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company_name": "Northbridge Landscaping & Snow",
        "domain": "northbridgelandscape.example",
        "snapshot": [
            {
                "text": "Serving the Valley since 1998, with about 40 year-round staff.",
                "source_urls": ["https://northbridgelandscape.example/about"],
                "confidence": "high",
            },
            {
                "text": "Headcount grows to roughly 140 in peak season.",
                "source_urls": ["https://northbridgelandscape.example/about"],
                "confidence": "high",
            },
            {
                "text": "Only yard and office is in Hadley, MA.",
                "source_urls": ["https://northbridgelandscape.example/about"],
                "confidence": "high",
            },
        ],
        "pain_point_hypotheses": [
            {
                "hypothesis": "Seasonal ramp-up creates a large onboarding and payroll setup burst each spring.",
                "rationale": "Headcount roughly triples between winter and peak season.",
                "evidence_urls": ["https://northbridgelandscape.example/about"],
                "confidence": "high",
            },
            {
                "hypothesis": "Overtime during peak season adds wage-calculation complexity.",
                "rationale": "The careers page advertises overtime during peak season.",
                "evidence_urls": ["https://northbridgelandscape.example/careers"],
                "confidence": "medium",
            },
        ],
        "discovery_questions": [
            "How do you onboard roughly 100 seasonal workers each spring?",
            "How is overtime tracked across crews?",
            "How do you handle the H-2B paperwork today?",
        ],
        "likely_objections": [
            {
                "objection": "Our payroll is simple.",
                "suggested_response": "Walk through the spring onboarding surge and ask where it gets stuck.",
            }
        ],
        "fit_score": 65,
        "fit_reasons": ["Large seasonal workforce in the home region."],
        "unverified": [
            "The careers page contained instruction-like text addressed to AI assistants; it was ignored."
        ],
    }
    base.update(overrides)
    return base


L1003_SCORE_ARGS = {
    "employee_count": 140,
    "states": ["MA"],
    "tipped_or_seasonal_workforce": True,
    "hiring_hr_or_payroll_roles": False,
    "industry": "Landscaping",
}
