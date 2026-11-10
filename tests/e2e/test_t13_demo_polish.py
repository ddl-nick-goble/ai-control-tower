"""T-13 e2e: demo polish for a model-risk audience.

data-testid contract: validation-card (agent page), dependency-model, finding-explanation-source,
finding-timeline-link, finding-item, evidence-pack-preview, criterion-<name> (#/register), source-filter, tier-badge.
"""
import re

from playwright.sync_api import expect


def _rid(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "risk-officer-1", "X-Domino-User": "risk.officer"})


def test_validation_card_shows_distribution(page, shot, seeded):
    page.goto(f"./#/agents/{seeded['recon_id']}")
    card = page.get_by_test_id("validation-card")
    expect(card).to_be_visible()
    for text in ("mean", "p5", "p95", "pass rate", "mock judge"):
        expect(card).to_contain_text(re.compile(re.escape(text), re.I))
    expect(card).to_contain_text(re.compile(r"20\s*prompts?\s*[x×]\s*5", re.I))
    expect(card).to_contain_text("0.825")
    expect(card).not_to_contain_text("No validation record")
    shot("then")


def test_validation_card_empty_state(page, shot, seeded):
    page.goto(f"./#/agents/{seeded['treasury_id']}")
    card = page.get_by_test_id("validation-card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("No validation record")
    shot("then")


def test_dependency_champion_label(page, shot, seeded):
    page.goto(f"./#/agents/{seeded['recon_id']}")
    dep = page.get_by_test_id("dependency-model").filter(has_text="break_classifier")
    expect(dep).to_have_count(1)
    expect(dep).to_contain_text(re.compile(r"version\s*5"))
    expect(dep).to_contain_text(re.compile(r"champion", re.I))
    shot("then")


def test_explanation_label_separate_from_timeline_link(page, shot, seeded):
    page.goto(f"./#/agents/{seeded['treasury_id']}")
    item = page.get_by_test_id("finding-item").filter(has_text="2026-11-07 02:14")
    expect(item).to_have_count(1)
    label = item.get_by_test_id("finding-explanation-source")
    link = item.get_by_test_id("finding-timeline-link")
    expect(label).to_be_visible()
    expect(link).to_be_visible()
    expect(link).to_have_text("View run timeline")
    assert "(no model)View" not in item.evaluate("e => e.innerText")
    lb, kb = label.bounding_box(), link.bounding_box()
    # visible separation: different lines, or a horizontal gap of at least 4px
    assert kb["y"] >= lb["y"] + lb["height"] - 1 or kb["x"] - (lb["x"] + lb["width"]) >= 4, (lb, kb)
    shot("then")


def _preview(page, aid):
    page.goto(f"./#/agents/{aid}")
    page.get_by_role("button", name="Generate evidence pack").click()
    prev = page.get_by_test_id("evidence-pack-preview")
    expect(prev).to_be_visible(timeout=15000)
    return prev


def test_evidence_preview_readable_violation_reasons(page, context, shot, seeded):
    _rid(context)
    prev = _preview(page, seeded["treasury_id"])
    txt = prev.inner_text()
    assert "tool_not_in_manifest" not in txt and "value_limit_exceeded" not in txt, txt
    assert "Outside manifest" in txt, txt
    prev = _preview(page, seeded["recon_id"])
    txt = prev.inner_text()
    assert "tool_not_in_manifest" not in txt and "value_limit_exceeded" not in txt, txt
    assert "Over limit" in txt or "Unverifiable value" in txt, txt
    shot("then")


def test_register_criteria_styled_like_fleet_filter(page, shot, seeded):
    # T-18: criteria are now segmented radiogroups; they must still be visibly styled controls (rounded, bordered).
    page.goto("./#/register")
    ctl = page.locator('[data-testid^="criterion-"][role=radiogroup]')
    expect(ctl.first).to_be_visible()
    n = ctl.count()
    assert n == 7
    for i in range(n):
        c = ctl.nth(i)
        opts = c.get_by_role("radio")
        assert opts.count() == 5
        radius = float(c.evaluate("e => parseFloat(getComputedStyle(e).borderTopLeftRadius) || 0")) or float(
            opts.first.evaluate("e => parseFloat(getComputedStyle(e).borderTopLeftRadius) || 0"))
        assert radius > 0, (c.get_attribute("data-testid"), radius)
    shot("then")


_LUM = """(rgb) => {
  const m = rgb.match(/[\\d.]+/g).map(Number);
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]);
}"""


def test_tier2_badge_contrast(page, shot, seeded):
    page.goto("./")
    badge = page.get_by_test_id("tier-badge").filter(has_text="Tier 2").first
    expect(badge).to_be_visible()
    fg, bg = badge.evaluate("e => { const s = getComputedStyle(e); return [s.color, s.backgroundColor]; }")
    l1, l2 = page.evaluate(f"[({_LUM})({fg!r}), ({_LUM})({bg!r})]")
    ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    assert ratio >= 4.5, f"Tier 2 badge contrast {ratio:.2f} ({fg} on {bg})"
    shot("then")
