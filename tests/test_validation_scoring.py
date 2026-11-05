from __future__ import annotations

import pytest
from helpers import brief_l1001

from salesprep.schemas import SalesBrief, brief_input_schema
from salesprep.scoring import score_fit
from salesprep.validation import check_brief, extract_numbers, normalize_url, ungrounded_numbers

PAGES = {
    "https://pvbrewing.example/about": "Founded in 2014 ... employs 85 people across 1,200 square feet.",
    "https://pvbrewing.example/": "Three taprooms.",
    "https://pvbrewing.example/careers": "HR & Payroll Coordinator. Three states.",
    "https://valleybiznews.example/pioneer-valley-brewing-opens-keene-taproom": "News.",
}


def test_normalize_url():
    assert normalize_url("HTTPS://PVBrewing.Example/about/#team") == "https://pvbrewing.example/about"
    assert normalize_url("https://pvbrewing.example") == "https://pvbrewing.example/"
    assert normalize_url("https://x.example/a?b=1") == "https://x.example/a?b=1"


def test_extract_numbers_handles_commas_and_decimals():
    assert extract_numbers("1,200 sq ft, 3.5 stars, founded 2014.") == {"1200", "3.5", "2014"}


def test_ungrounded_numbers():
    assert ungrounded_numbers("85 people", ["employs 85 people"]) == set()
    assert ungrounded_numbers("400 people in 2014", ["employs 85 people, founded 2014"]) == {"400"}


def test_valid_brief_passes():
    brief = SalesBrief.model_validate(brief_l1001())
    assert check_brief(brief, PAGES, "pvbrewing.example", {"score": 100}) == []


def test_requires_score_fit_and_matching_score():
    brief = SalesBrief.model_validate(brief_l1001())
    assert any("call score_fit" in e for e in check_brief(brief, PAGES, "pvbrewing.example", None))
    assert any("returned 70" in e for e in check_brief(brief, PAGES, "pvbrewing.example", {"score": 70}))


def test_domain_mismatch():
    brief = SalesBrief.model_validate(brief_l1001())
    assert any("domain must be" in e for e in check_brief(brief, PAGES, "other.example", {"score": 100}))


def test_unfetched_citation_in_claim_and_hypothesis():
    pages = {k: v for k, v in PAGES.items() if "valleybiznews" not in k and k != "https://pvbrewing.example/"}
    errors = check_brief(SalesBrief.model_validate(brief_l1001()), pages, "pvbrewing.example", {"score": 100})
    assert any(e.startswith("snapshot[2]") for e in errors)
    assert any(e.startswith("pain_point_hypotheses[1]") for e in errors)


def test_number_not_in_cited_page_is_flagged():
    data = brief_l1001()
    data["snapshot"][0]["text"] = "Employs 90 people."
    errors = check_brief(SalesBrief.model_validate(data), PAGES, "pvbrewing.example", {"score": 100})
    assert any("['90']" in e for e in errors)


def test_url_matching_ignores_trailing_slash_and_case():
    data = brief_l1001()
    data["snapshot"][0]["source_urls"] = ["https://PVBREWING.example/about/"]
    assert check_brief(SalesBrief.model_validate(data), PAGES, "pvbrewing.example", {"score": 100}) == []


def test_high_confidence_hypothesis_needs_evidence():
    data = brief_l1001()
    data["pain_point_hypotheses"][0]["evidence_urls"] = []
    with pytest.raises(ValueError, match="evidence URL"):
        SalesBrief.model_validate(data)


def test_brief_tool_schema_is_self_contained():
    schema = brief_input_schema()
    assert schema["type"] == "object"
    assert "$defs" not in str(schema) and "$ref" not in str(schema)
    assert {"snapshot", "fit_score", "domain"} <= set(schema["properties"])


# ---------------------------------------------------------------- scoring


def test_scoring_brewery_is_max():
    result = score_fit(
        employee_count=85, states=["MA", "VT", "NH"], tipped_or_seasonal_workforce=True,
        hiring_hr_or_payroll_roles=True, industry="Craft brewery",
    )
    assert result["score"] == 100


def test_scoring_dental_and_studio():
    dental = score_fit(employee_count=124, states=["ma", "ct"], tipped_or_seasonal_workforce=False,
                       hiring_hr_or_payroll_roles=True, industry="Dental group")
    assert dental["score"] == 80
    studio = score_fit(employee_count=4, states=["RI"], tipped_or_seasonal_workforce=False,
                       hiring_hr_or_payroll_roles=False, industry="Design services")
    assert studio["score"] == 5


def test_scoring_unknown_headcount_and_custom_territory():
    result = score_fit(employee_count=None, states=["TX"], tipped_or_seasonal_workforce=False,
                       hiring_hr_or_payroll_roles=False, industry=None, home_region_states=("TX",))
    assert result["score"] == 10  # territory only
    assert result["breakdown"][0]["points"] == 0


@pytest.mark.parametrize("kwargs", [
    {"employee_count": -1, "states": ["MA"]},
    {"employee_count": 5, "states": ["Massachusetts"]},
    {"employee_count": 5, "states": "MA"},
])
def test_scoring_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        score_fit(tipped_or_seasonal_workforce=False, hiring_hr_or_payroll_roles=False, industry="x", **kwargs)
