from __future__ import annotations

import json

import pytest

from salesprep.audit import AuditLog, redact, redact_obj
from salesprep.crm import DisallowedFieldError


def test_agent_view_hides_contact_fields(stores):
    crm, _ = stores
    full = crm.get_lead("L-1001")
    view = crm.agent_view("L-1001")
    assert "contact_email" in full and "contact_phone" in full
    assert "contact_email" not in view and "contact_phone" not in view
    assert view["company_name"] == "Pioneer Valley Brewing Co."
    assert crm.agent_view("nope") is None


def test_propose_then_approve_applies_update(stores):
    crm, approvals = stores
    approval_id = approvals.propose(
        "L-1001",
        {"employee_count": 85, "enrichment_summary": "Per About page."},
        "stale headcount",
        "run1",
    )
    assert crm.get_lead("L-1001")["employee_count"] == 60
    outcome = approvals.decide(approval_id, True, crm)
    assert outcome["status"] == "approved"
    lead = crm.get_lead("L-1001")
    assert (
        lead["employee_count"] == 85 and lead["enrichment_summary"] == "Per About page."
    )
    assert approvals.list("pending") == []


def test_reject_does_not_apply(stores):
    crm, approvals = stores
    approval_id = approvals.propose("L-1001", {"employee_count": 999}, "bad idea")
    approvals.decide(approval_id, False, crm)
    assert crm.get_lead("L-1001")["employee_count"] == 60
    assert approvals.list("rejected")[0]["id"] == approval_id


def test_cannot_decide_twice_or_unknown(stores):
    crm, approvals = stores
    approval_id = approvals.propose("L-1001", {"notes": "hi"}, "reason")
    approvals.decide(approval_id, True, crm)
    with pytest.raises(ValueError, match="already approved"):
        approvals.decide(approval_id, False, crm)
    with pytest.raises(ValueError, match="not found"):
        approvals.decide(999, True, crm)


@pytest.mark.parametrize(
    "fields", [{"status": "closed-won"}, {"contact_email": "x@y.z"}, {"id": "L-1"}]
)
def test_disallowed_fields_rejected(stores, fields):
    _, approvals = stores
    with pytest.raises(DisallowedFieldError):
        approvals.propose("L-1001", fields, "reason")
    assert approvals.list(None) == []


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"employee_count": "85"},
        {"employee_count": -1},
        {"fit_score": 101},
        {"notes": "x" * 1001},
        {"employee_count": True},
    ],
)
def test_invalid_values_rejected(stores, fields):
    _, approvals = stores
    with pytest.raises(ValueError):
        approvals.propose("L-1001", fields, "reason")


def test_propose_requires_reason_and_known_lead(stores):
    _, approvals = stores
    with pytest.raises(ValueError, match="reason"):
        approvals.propose("L-1001", {"notes": "x"}, "  ")
    with pytest.raises(ValueError, match="unknown lead"):
        approvals.propose("L-9999", {"notes": "x"}, "reason")


def test_apply_update_is_allowlisted_even_if_called_directly(stores):
    crm, _ = stores
    with pytest.raises(DisallowedFieldError):
        crm.apply_update("L-1001", {"status": "closed-won"})
    assert crm.get_lead("L-1001")["status"] == "new"


def test_sql_injection_in_values_is_inert(stores):
    crm, approvals = stores
    approval_id = approvals.propose(
        "L-1001", {"notes": "x'; DROP TABLE leads; --"}, "reason"
    )
    approvals.decide(approval_id, True, crm)
    assert crm.get_lead("L-1001")["notes"] == "x'; DROP TABLE leads; --"
    assert crm.get_lead("L-1002") is not None


# ---------------------------------------------------------------- audit / redaction


def test_redact_patterns():
    text = "Reach jo@acme.com or (413) 555-0142, SSN 123-45-6789, EIN 12-3456789."
    out = redact(text)
    assert "jo@acme.com" not in out and "555-0142" not in out
    assert "123-45-6789" not in out and "12-3456789" not in out
    assert (
        out.count("[EMAIL]") == 1
        and "[SSN]" in out
        and "[EIN]" in out
        and "[PHONE]" in out
    )


def test_redact_leaves_ordinary_numbers_alone():
    assert (
        redact("Founded in 2014 with 85 employees across 3 sites")
        == "Founded in 2014 with 85 employees across 3 sites"
    )


def test_redact_obj_nested_and_truncates():
    out = redact_obj({"a": ["x@y.com", {"b": "z" * 5000}], "n": 5})
    assert out["a"][0] == "[EMAIL]"
    assert out["a"][1]["b"].endswith("…[truncated]") and out["n"] == 5


def test_audit_log_writes_redacted_jsonl(tmp_path):
    log = AuditLog(tmp_path / "run1")
    log.log("tool_call", tool="fetch_page", args={"note": "contact ceo@acme.com"})
    log.log("run_finished", status="completed")
    lines = (tmp_path / "run1" / "audit.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    assert (
        first["event"] == "tool_call"
        and "ceo@acme.com" not in lines[0]
        and "[EMAIL]" in lines[0]
    )
    assert json.loads(lines[1])["status"] == "completed" and "ts" in first
