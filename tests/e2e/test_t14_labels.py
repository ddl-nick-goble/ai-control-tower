"""T-14 e2e: template (non-model) explanations are labelled in the console.

data-testid contract: finding-explanation-source (inside finding-item), text contains "template text"
when the finding detail has no explanation_model.
"""
from playwright.sync_api import expect


def test_ac7_template_explanation_labelled(page, shot, seeded):
    page.goto(f"./#/agents/{seeded['treasury_id']}")
    finding = page.get_by_test_id("finding-item").filter(has_text="2026-11-07 02:14")
    expect(finding).to_have_count(1)
    api = page.request.get(f"./api/agents/{seeded['treasury_id']}/findings").json()
    f = next(x for x in api if x["type"] == "scope_drift")
    assert not (f.get("detail") or {}).get("explanation_model")
    label = finding.get_by_test_id("finding-explanation-source")
    expect(label).to_be_visible()
    expect(label).to_contain_text("template text")
    expect(finding).to_contain_text("4750000")
    shot("then")
