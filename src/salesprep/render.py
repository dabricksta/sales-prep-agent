"""Render a validated brief as a one-page markdown document with numbered sources."""

from __future__ import annotations

from .schemas import SalesBrief
from .validation import normalize_url


def brief_to_markdown(brief: SalesBrief) -> str:
    sources: list[str] = []
    index: dict[str, int] = {}

    def cite(urls: list[str]) -> str:
        marks = []
        for url in urls:
            key = normalize_url(url)
            if key not in index:
                sources.append(url)
                index[key] = len(sources)
            marks.append(f"[{index[key]}]")
        return "".join(marks)

    lines = [f"# Call prep: {brief.company_name}", "", f"**Domain:** {brief.domain}  ", f"**Fit score:** {brief.fit_score}/100", ""]
    lines += ["## Why this score", ""] + [f"- {reason}" for reason in brief.fit_reasons] + [""]

    lines += ["## Company snapshot (sourced facts)", ""]
    for claim in brief.snapshot:
        lines.append(f"- {claim.text} {cite(claim.source_urls)} _(confidence: {claim.confidence})_")
    lines.append("")

    lines += ["## Pain-point hypotheses (inferences, not facts)", ""]
    for hyp in brief.pain_point_hypotheses:
        evidence = f" {cite(hyp.evidence_urls)}" if hyp.evidence_urls else ""
        lines.append(f"- **{hyp.hypothesis}** _(confidence: {hyp.confidence})_{evidence}")
        lines.append(f"  - Why: {hyp.rationale}")
    lines.append("")

    lines += ["## Discovery questions", ""] + [f"{i}. {q}" for i, q in enumerate(brief.discovery_questions, start=1)] + [""]

    lines += ["## Likely objections", ""]
    for obj in brief.likely_objections:
        lines.append(f"- **{obj.objection}**")
        lines.append(f"  - Response: {obj.suggested_response}")
    lines.append("")

    if brief.unverified:
        lines += ["## Unverified / flags", ""] + [f"- {item}" for item in brief.unverified] + [""]

    lines += ["## Sources", ""] + [f"{i}. {url}" for i, url in enumerate(sources, start=1)]
    return "\n".join(lines) + "\n"
