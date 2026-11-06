"""Brief validation: the anti-hallucination layer.

Rules enforced before a brief is accepted:
1. Every cited URL must be a page the agent actually fetched during this run.
2. Every number in a snapshot claim must appear in at least one of its cited pages.
3. fit_score must equal the deterministic score_fit result (and score_fit must have run).
4. The brief's domain must match the lead's domain.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .schemas import SalesBrief

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


def extract_numbers(text: str) -> set[str]:
    return {m.group().rstrip(",").replace(",", "") for m in _NUM_RE.finditer(text)}


def ungrounded_numbers(claim_text: str, cited_page_texts: list[str]) -> set[str]:
    """Numbers in the claim that none of the cited pages contain."""
    available: set[str] = set()
    for page in cited_page_texts:
        available |= extract_numbers(page)
    return extract_numbers(claim_text) - available


def check_brief(
    brief: SalesBrief,
    pages: dict[str, str],
    lead_domain: str,
    last_score: dict[str, Any] | None,
) -> list[str]:
    """Return a list of instructive error strings (empty means the brief passes)."""
    errors: list[str] = []
    fetched = {normalize_url(url): text for url, text in pages.items()}

    if brief.domain.strip().lower() != lead_domain.strip().lower():
        errors.append(
            f"domain must be '{lead_domain}' (the lead's domain), got '{brief.domain}'."
        )

    if last_score is None:
        errors.append(
            "call score_fit before submit_brief; fit_score must come from that tool."
        )
    elif brief.fit_score != last_score["score"]:
        errors.append(
            f"fit_score is {brief.fit_score} but score_fit returned {last_score['score']}. Use the tool's value."
        )

    def unfetched(urls: list[str]) -> list[str]:
        return [u for u in urls if normalize_url(u) not in fetched]

    for i, claim in enumerate(brief.snapshot, start=1):
        missing = unfetched(claim.source_urls)
        if missing:
            errors.append(
                f"snapshot[{i}] cites URL(s) you never fetched: {', '.join(missing)}. "
                "Call fetch_page on a URL before citing it (search results alone are not enough)."
            )
            continue
        texts = [fetched[normalize_url(u)] for u in claim.source_urls]
        bad_numbers = ungrounded_numbers(claim.text, texts)
        if bad_numbers:
            errors.append(
                f"snapshot[{i}] contains number(s) {sorted(bad_numbers)} that do not appear in its cited pages. "
                "Quote numbers exactly as the source writes them, or cite the page that contains them."
            )

    for i, hyp in enumerate(brief.pain_point_hypotheses, start=1):
        missing = unfetched(hyp.evidence_urls)
        if missing:
            errors.append(
                f"pain_point_hypotheses[{i}] cites URL(s) you never fetched: {', '.join(missing)}."
            )

    return errors
