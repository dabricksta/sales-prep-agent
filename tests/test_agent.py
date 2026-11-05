from __future__ import annotations

import dataclasses
import json

from helpers import (
    L1001_URLS,
    L1003_SCORE_ARGS,
    L1003_URLS,
    FakeClient,
    brief_l1001,
    brief_l1003,
    reply,
    script_l1001,
    text,
    tool_use,
)

from salesprep.agent import SalesPrepAgent


def make_agent(client, config, stores):
    crm, approvals = stores
    return SalesPrepAgent(client, config, crm, approvals)


def test_happy_path_completes_and_writes_outputs(config, stores):
    client = FakeClient(script_l1001())
    result = make_agent(client, config, stores).run("L-1001")

    assert result.status == "completed"
    assert result.steps == 5
    assert result.brief is not None and result.brief.fit_score == 100
    assert result.tool_calls == ["crm_get_lead", "search_web", "fetch_page", "fetch_page", "fetch_page", "fetch_page", "score_fit", "submit_brief"]
    assert result.violations == []
    assert (result.run_dir / "brief.md").exists()
    assert json.loads((result.run_dir / "brief.json").read_text())["domain"] == "pvbrewing.example"
    assert json.loads((result.run_dir / "summary.json").read_text())["status"] == "completed"
    assert (result.run_dir / "audit.jsonl").exists()


def test_tool_results_follow_api_contract(config, stores):
    client = FakeClient(script_l1001())
    make_agent(client, config, stores).run("L-1001")

    # The 4th request carries the results of the 4 parallel fetch_page calls.
    messages = client.calls[3]["messages"]
    assert messages[-2]["role"] == "assistant"
    last = messages[-1]
    assert last["role"] == "user"
    assert [b["type"] for b in last["content"]] == ["tool_result"] * 4
    assert [b["tool_use_id"] for b in last["content"]] == ["f0", "f1", "f2", "f3"]
    assert all("<untrusted_content>" in b["content"] for b in last["content"])
    assert all("is_error" not in b for b in last["content"])


def test_requests_carry_system_prompt_and_tools(config, stores):
    client = FakeClient(script_l1001())
    make_agent(client, config, stores).run("L-1001")
    first = client.calls[0]
    assert first["model"] == config.model
    assert "untrusted_content" in first["system"]
    names = {t["name"] for t in first["tools"]}
    assert names == {"crm_get_lead", "search_web", "fetch_page", "score_fit", "propose_crm_update", "submit_brief"}
    # No tool exists that could send email or write to the CRM directly.
    assert not any("send" in n or "update_lead" in n for n in names)


def test_rejected_brief_returns_is_error_and_agent_recovers(config, stores):
    bad = brief_l1001()
    bad["snapshot"][0]["source_urls"] = ["https://pvbrewing.example/never-fetched"]
    script = script_l1001(bad)
    script.append(reply([tool_use("submit_brief", brief_l1001(), "t6")]))
    client = FakeClient(script)
    result = make_agent(client, config, stores).run("L-1001")

    assert result.status == "completed"
    assert result.steps == 6
    error_block = client.calls[5]["messages"][-1]["content"][0]
    assert error_block["is_error"] is True
    assert "never fetched" in error_block["content"]


def test_hallucinated_number_is_rejected(config, stores):
    bad = brief_l1001()
    bad["snapshot"][0]["text"] = "Founded in 2014 and now employs 400 people across three taprooms."
    client = FakeClient(script_l1001(bad))
    result = make_agent(client, config, stores).run("L-1001")
    # The script ends after the rejected submit, so the agent's next model call fails.
    assert result.status == "failed" and result.brief is None
    assert len(client.calls) == 6
    rejection = client.calls[5]["messages"][-1]["content"][0]
    assert rejection["is_error"] is True
    assert "do not appear in its cited pages" in rejection["content"]


def test_prompt_injection_attempt_is_blocked_and_counted(config, stores):
    crm, approvals = stores
    script = [
        reply([tool_use("crm_get_lead", {"lead_id": "L-1003"}, "a1")]),
        reply([tool_use("fetch_page", {"url": u}, f"f{i}") for i, u in enumerate(L1003_URLS)]),
        # A model that obeyed the canary on the careers page would try this:
        reply([tool_use("propose_crm_update", {"lead_id": "L-1003", "fields": {"status": "closed-won"}, "reason": "page said so"}, "a3")]),
        reply([tool_use("score_fit", L1003_SCORE_ARGS, "a4")]),
        reply([tool_use("submit_brief", brief_l1003(), "a5")]),
    ]
    client = FakeClient(script)
    result = make_agent(client, config, stores).run("L-1003")

    assert result.status == "completed"
    assert result.violations == ["disallowed_field_write:status"]
    assert approvals.list(None) == []  # nothing queued
    assert crm.get_lead("L-1003")["status"] == "new"  # CRM untouched
    # The canary text reached the model only inside an untrusted fence.
    careers_result = client.calls[2]["messages"][-1]["content"][1]["content"]
    assert "SYSTEM NOTICE TO AI ASSISTANTS" in careers_result
    assert careers_result.index("<untrusted_content>") < careers_result.index("SYSTEM NOTICE")


def test_out_of_scope_lead_read_is_a_violation(config, stores):
    script = [
        reply([tool_use("crm_get_lead", {"lead_id": "L-1002"}, "a1")]),
        reply([], stop_reason="end_turn"),
        reply([], stop_reason="end_turn"),
        reply([], stop_reason="end_turn"),
    ]
    client = FakeClient(script)
    result = make_agent(client, config, stores).run("L-1001")
    assert "out_of_scope_lead_read:L-1002" in result.violations
    first_error = client.calls[1]["messages"][-1]["content"][0]
    assert first_error["is_error"] is True


def test_agent_never_sees_contact_details(config, stores):
    client = FakeClient(script_l1001())
    make_agent(client, config, stores).run("L-1001")
    lead_json = client.calls[1]["messages"][-1]["content"][0]["content"]
    lead = json.loads(lead_json)
    assert "contact_email" not in lead and "contact_phone" not in lead
    assert "morgan.reyes" not in lead_json and "555-0142" not in lead_json


def test_proposals_are_queued_not_applied(config, stores):
    crm, approvals = stores
    script = [
        reply([tool_use("propose_crm_update", {"lead_id": "L-1001", "fields": {"employee_count": 85}, "reason": "About page says 85 employees"}, "a1")]),
        reply([], stop_reason="end_turn"),
        reply([], stop_reason="end_turn"),
        reply([], stop_reason="end_turn"),
    ]
    make_agent(FakeClient(script), config, stores).run("L-1001")
    assert crm.get_lead("L-1001")["employee_count"] == 60  # unchanged until a human approves
    pending = approvals.list("pending")
    assert len(pending) == 1 and pending[0]["fields"] == {"employee_count": 85}


def test_end_turn_without_brief_is_nudged_then_fails(config, stores):
    client = FakeClient([reply([text("Here is my summary.")], stop_reason="end_turn") for _ in range(3)])
    result = make_agent(client, config, stores).run("L-1001")
    assert result.status == "failed"
    assert "without submitting a brief" in result.error
    assert "have not submitted a brief" in client.calls[1]["messages"][-1]["content"]


def test_max_tokens_truncation_retries_with_larger_budget(config, stores):
    truncated = reply([tool_use("fetch_page", {"url": "https://pvbr"}, "x")], stop_reason="max_tokens")
    client = FakeClient([truncated] + script_l1001())
    result = make_agent(client, config, stores).run("L-1001")
    assert result.status == "completed"
    assert client.calls[1]["max_tokens"] == client.calls[0]["max_tokens"] * 2
    # The truncated assistant turn was dropped, not replayed.
    assert len(client.calls[1]["messages"]) == len(client.calls[0]["messages"])


def test_step_limit(config, stores):
    cfg = dataclasses.replace(config, max_steps=2)
    script = [reply([tool_use("search_web", {"query": "payroll"}, f"s{i}")]) for i in range(2)]
    result = make_agent(FakeClient(script), cfg, stores).run("L-1001")
    assert result.status == "max_steps"


def test_token_budget(config, stores):
    cfg = dataclasses.replace(config, max_total_tokens=100)
    script = [reply([tool_use("crm_get_lead", {"lead_id": "L-1001"}, "a1")], input_tokens=90, output_tokens=20)]
    result = make_agent(FakeClient(script), cfg, stores).run("L-1001")
    assert result.status == "budget_exceeded"
    assert result.steps == 1


def test_tool_call_budget(config, stores):
    cfg = dataclasses.replace(config, max_tool_calls=1)
    script = [
        reply([tool_use("crm_get_lead", {"lead_id": "L-1001"}, "a1"), tool_use("search_web", {"query": "payroll"}, "a2")]),
        reply([], stop_reason="end_turn"),
        reply([], stop_reason="end_turn"),
        reply([], stop_reason="end_turn"),
    ]
    client = FakeClient(script)
    make_agent(client, cfg, stores).run("L-1001")
    blocks = client.calls[1]["messages"][-1]["content"]
    assert "is_error" not in blocks[0]
    assert blocks[1]["is_error"] is True and "budget" in blocks[1]["content"].lower()


def test_api_failure_is_reported_and_redacted(config, stores):
    client = FakeClient([RuntimeError("upstream said no for ops@example.com")])
    result = make_agent(client, config, stores).run("L-1001")
    assert result.status == "failed"
    assert "ops@example.com" not in result.error and "[EMAIL]" in result.error


def test_unknown_lead(config, stores):
    result = make_agent(FakeClient([]), config, stores).run("L-9999")
    assert result.status == "failed" and "not found" in result.error


def test_unknown_tool_name_is_an_error_result(config, stores):
    script = [reply([tool_use("send_email", {"to": "x@y.z"}, "a1")])] + [reply([], stop_reason="end_turn") for _ in range(3)]
    client = FakeClient(script)
    make_agent(client, config, stores).run("L-1001")
    block = client.calls[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "Unknown tool" in block["content"]
