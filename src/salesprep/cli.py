"""Command-line interface: seed, run, serve, approvals, eval."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from typing import Any

from .agent import SalesPrepAgent
from .config import Config
from .crm import CRM, ApprovalQueue


def _make_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Export it (see .env.example) and retry.", file=sys.stderr)
        raise SystemExit(2)
    import anthropic

    return anthropic.Anthropic()


def _stores(config: Config) -> tuple[CRM, ApprovalQueue]:
    return CRM(config.db_path), ApprovalQueue(config.db_path)


def _seed(config: Config) -> int:
    crm, _ = _stores(config)
    leads = json.loads(config.seed_path.read_text(encoding="utf-8"))
    return crm.seed(leads)


def cmd_seed(config: Config, args: argparse.Namespace) -> int:
    count = _seed(config)
    print(f"Seeded {count} synthetic leads into {config.db_path}")
    return 0


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    if args.web:
        config = dataclasses.replace(config, web_mode=args.web)
    if args.model:
        config = dataclasses.replace(config, model=args.model)
    crm, approvals = _stores(config)
    if crm.get_lead(args.lead_id) is None:
        print(f"Lead {args.lead_id} not found. Run `salesprep seed` first?", file=sys.stderr)
        return 1
    agent = SalesPrepAgent(_make_client(), config, crm, approvals)
    result = agent.run(args.lead_id)
    print(f"run {result.run_id}: {result.status} in {result.steps} steps, {result.input_tokens}+{result.output_tokens} tokens")
    if result.violations:
        print(f"policy violations blocked: {result.violations}")
    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
    if result.brief is not None:
        print(f"brief: {result.run_dir / 'brief.md'}")
        if args.show:
            print()
            print((result.run_dir / "brief.md").read_text(encoding="utf-8"))
    pending = [a for a in approvals.list("pending") if a["run_id"] == result.run_id]
    if pending:
        print(f"{len(pending)} CRM update proposal(s) awaiting review: `salesprep approvals list`")
    return 0 if result.status == "completed" else 1


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    from .server import WebhookApp, serve

    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        print("WEBHOOK_SECRET is not set; refusing to start an unauthenticated webhook.", file=sys.stderr)
        return 2
    crm, approvals = _stores(config)
    agent = SalesPrepAgent(_make_client(), config, crm, approvals)

    def run_agent(lead_id: str, run_id: str) -> None:
        result = agent.run(lead_id, run_id=run_id)
        print(f"[{time.strftime('%H:%M:%S')}] run {run_id} lead {lead_id}: {result.status}", flush=True)

    app = WebhookApp(
        secret=secret,
        lead_exists=lambda lead_id: crm.get_lead(lead_id) is not None,
        run_agent=run_agent,
        max_skew=config.webhook_max_skew_seconds,
    )
    serve(app, host=args.host, port=args.port)
    return 0


def cmd_approvals(config: Config, args: argparse.Namespace) -> int:
    crm, approvals = _stores(config)
    if args.action == "list":
        rows = approvals.list(None if args.all else "pending")
        if not rows:
            print("No approvals." if args.all else "No pending approvals.")
        for row in rows:
            print(f"#{row['id']} [{row['status']}] lead {row['lead_id']} run {row['run_id']}")
            print(f"    fields: {json.dumps(row['fields'])}")
            print(f"    reason: {row['reason']}")
        return 0
    try:
        outcome = approvals.decide(args.id, approve=(args.action == "approve"), crm=crm)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"approval #{outcome['id']} {outcome['status']} (lead {outcome['lead_id']})")
    return 0


def cmd_eval(config: Config, args: argparse.Namespace) -> int:
    from .evals import format_report, load_cases, run_evals

    # Isolated workspace so evals never touch your working CRM or approval queue.
    eval_config = dataclasses.replace(config, root=config.var_dir / "eval_workspace")
    _seed(eval_config)
    crm, approvals = _stores(eval_config)
    cases = load_cases(args.cases or (config.data_dir / "eval_cases.json"))
    if args.lead:
        cases = [c for c in cases if c["lead_id"] == args.lead]
        if not cases:
            print(f"No eval case for {args.lead}", file=sys.stderr)
            return 1
    agent = SalesPrepAgent(_make_client(), eval_config, crm, approvals)
    report = run_evals(agent, cases)
    print(format_report(report))
    out_dir = config.var_dir / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report: {out}")
    return 0 if report["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="salesprep", description="Claude-based sales call-prep agent (synthetic data demo).")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="load synthetic leads into the mock CRM")

    p_run = sub.add_parser("run", help="run the agent for one lead")
    p_run.add_argument("lead_id")
    p_run.add_argument("--web", choices=["fixture", "live"], help="override SALESPREP_WEB")
    p_run.add_argument("--model", help="override SALESPREP_MODEL")
    p_run.add_argument("--show", action="store_true", help="print the brief when done")

    p_serve = sub.add_parser("serve", help="start the signed webhook trigger")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)

    p_app = sub.add_parser("approvals", help="review CRM updates proposed by the agent")
    p_app_sub = p_app.add_subparsers(dest="action", required=True)
    p_list = p_app_sub.add_parser("list")
    p_list.add_argument("--all", action="store_true", help="include decided proposals")
    for name in ("approve", "reject"):
        p = p_app_sub.add_parser(name)
        p.add_argument("id", type=int)

    p_eval = sub.add_parser("eval", help="run the eval suite against the live API")
    p_eval.add_argument("--cases", type=lambda s: __import__("pathlib").Path(s))
    p_eval.add_argument("--lead", help="run a single case by lead id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.from_env()
    handlers = {
        "seed": cmd_seed,
        "run": cmd_run,
        "serve": cmd_serve,
        "approvals": cmd_approvals,
        "eval": cmd_eval,
    }
    return handlers[args.command](config, args)


if __name__ == "__main__":
    raise SystemExit(main())
