from __future__ import annotations

import json

import pytest
from helpers import FakeClient, brief_l1001, script_l1001

from salesprep.agent import SalesPrepAgent
from salesprep.evals import check_case, format_report, load_cases, run_evals
from salesprep.render import brief_to_markdown
from salesprep.schemas import SalesBrief
from salesprep.server import MAX_BODY_BYTES, WebhookApp, sign, verify

SECRET = "s3cret"
NOW = 1_800_000_000.0


def signed(body: dict | bytes, ts: float = NOW, secret: str = SECRET):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    stamp = str(int(ts))
    return raw, {"X-Timestamp": stamp, "X-Signature": sign(secret.encode(), stamp, raw)}


@pytest.fixture
def app_and_calls():
    calls: list[tuple[str, str]] = []
    app = WebhookApp(SECRET, lambda lead_id: lead_id == "L-1001", lambda lead_id, run_id: calls.append((lead_id, run_id)), clock=lambda: NOW)
    yield app, calls
    app.shutdown(wait=True)


def test_verify_signature_and_skew():
    body = b'{"lead_id":"L-1001"}'
    sig = sign(b"k", "1000", body)
    assert verify(b"k", "1000", body, sig, now=1100, max_skew=300)
    assert not verify(b"k", "1000", body, sig, now=1400, max_skew=300)  # too old
    assert not verify(b"other", "1000", body, sig, now=1000, max_skew=300)
    assert not verify(b"k", "1000", body + b" ", sig, now=1000, max_skew=300)  # tampered body
    assert not verify(b"k", "abc", body, sig, now=1000, max_skew=300)
    assert not verify(b"k", "1000", body, "", now=1000, max_skew=300)


def test_webhook_accepts_signed_request_and_runs_agent(app_and_calls):
    app, calls = app_and_calls
    body, headers = signed({"lead_id": "L-1001"})
    status, payload = app.handle("POST", "/webhook/new-lead", headers, body)
    assert status == 202 and payload["accepted"] is True
    app.shutdown(wait=True)
    assert calls == [("L-1001", payload["run_id"])]


def test_webhook_rejections(app_and_calls):
    app, calls = app_and_calls
    body, headers = signed({"lead_id": "L-1001"})
    assert app.handle("POST", "/webhook/new-lead", {}, body)[0] == 401  # unsigned
    assert app.handle("POST", "/webhook/new-lead", {**headers, "X-Signature": "sha256=bad"}, body)[0] == 401
    old_body, old_headers = signed({"lead_id": "L-1001"}, ts=NOW - 10_000)
    assert app.handle("POST", "/webhook/new-lead", old_headers, old_body)[0] == 401  # replay window
    assert app.handle("POST", "/webhook/new-lead", signed({"lead_id": "L-9999"})[1], signed({"lead_id": "L-9999"})[0])[0] == 404
    for bad in ({"lead_id": "../etc"}, {"lead_id": 5}, {"other": 1}):
        raw, hdrs = signed(bad)
        assert app.handle("POST", "/webhook/new-lead", hdrs, raw)[0] == 400
    raw, hdrs = signed(b"not json")
    assert app.handle("POST", "/webhook/new-lead", hdrs, raw)[0] == 400
    big, big_headers = signed(b"x" * (MAX_BODY_BYTES + 1))
    assert app.handle("POST", "/webhook/new-lead", big_headers, big)[0] == 413
    assert app.handle("GET", "/webhook/new-lead", headers, body)[0] == 404
    assert app.handle("POST", "/other", headers, body)[0] == 404
    app.shutdown(wait=True)
    assert calls == []


def test_webhook_refuses_empty_secret():
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        WebhookApp("", lambda _: True, lambda *_: None)


def test_render_numbers_and_dedupes_sources():
    md = brief_to_markdown(SalesBrief.model_validate(brief_l1001()))
    assert "# Call prep: Pioneer Valley Brewing Co." in md
    assert "**Fit score:** 100/100" in md
    # The About page backs a claim and a hypothesis but appears once in the source list.
    sources = md.split("## Sources")[1]
    assert md.count("https://pvbrewing.example/about") == 1
    assert "1. https://pvbrewing.example/about" in sources
    assert "[1]" in md.split("## Company snapshot")[1].split("## Pain-point")[0]
    assert "(inferences, not facts)" in md


# ---------------------------------------------------------------- evals


def test_eval_harness_passes_and_fails_correctly(config, stores):
    crm, approvals = stores
    agent = SalesPrepAgent(FakeClient(script_l1001()), config, crm, approvals)
    cases = load_cases(config.data_dir / "eval_cases.json")
    assert [c["lead_id"] for c in cases] == ["L-1001", "L-1002", "L-1003", "L-1004"]

    report = run_evals(agent, cases[:1])
    assert report["passed"] and report["cases_passed"] == 1
    assert "PASS  L-1001" in format_report(report)

    run = SalesPrepAgent(FakeClient(script_l1001()), config, crm, approvals).run("L-1001")
    wrong_band = {**cases[3]}  # the "poor fit" case: expects 0-20, brief says 100
    checks = {c["name"]: c for c in check_case(wrong_band, run)}
    assert checks["fit_in_band"]["passed"] is False
    assert checks["cites_sparrowstudio.example"]["passed"] is False


def test_eval_flags_policy_violations_and_forbidden_text(config, stores):
    crm, approvals = stores
    run = SalesPrepAgent(FakeClient(script_l1001()), config, crm, approvals).run("L-1001")
    run.violations.append("disallowed_field_write:status")
    case = {"lead_id": "L-1001", "fit_min": 0, "fit_max": 100, "forbidden_substrings": ["biweekly payroll"]}
    checks = {c["name"]: c for c in check_case(case, run)}
    assert checks["no_policy_violations"]["passed"] is False
    assert checks["absent:biweekly payroll"]["passed"] is False
