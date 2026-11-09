"""T-10 evidence pack e2e (AC-7, AC-12).

data-testid contract (agent page #/agents/<id>):
  evidence-pack-generate   button, accessible name "Generate evidence pack"
  evidence-pack-result     result panel shown after generation (within 15 s)
  evidence-pack-md-link    download link (href) for the Markdown file
  evidence-pack-pdf-link   download link (href) for the PDF file
  evidence-pack-preview    Markdown rendered as formatted HTML (h1-h3 headings, no raw "## ")
  evidence-pack-bundle     bundle id, or a "saved to dataset" note
Also uses T-9 ids: fleet-row, tier-badge, open-findings, finding-item, finding-timeline-link, timeline-page.
Markdown headings must appear in the 2026-54 order: identity and ownership, tier and criteria,
permitted actions, validation, monitoring and findings, approvals and overrides, records and retention.
"""
import re

import pytest
from playwright.sync_api import expect

SECTIONS = [r"identity", r"tier", r"permitted actions", r"validation", r"monitoring", r"approvals", r"records"]


def _generate(page, seeded):
    page.goto(f"./#/agents/{seeded['treasury_id']}")
    btn = page.get_by_role("button", name="Generate evidence pack")
    expect(btn).to_be_visible()
    expect(page.get_by_test_id("evidence-pack-generate")).to_be_visible()
    btn.click()
    expect(page.get_by_test_id("evidence-pack-result")).to_be_visible(timeout=15000)


def _monitoring_text(preview):
    h = preview.locator("h1,h2,h3,h4").filter(has_text=re.compile("monitoring", re.I)).first
    expect(h).to_be_visible()
    # text of the preview from the monitoring heading up to the next heading of the same section list
    txt = preview.inner_text()
    i = re.search(r"monitoring", txt, re.I).start()
    m = re.search(r"approvals", txt[i:], re.I)
    return txt[i:i + m.start()] if m else txt[i:]


def test_ac7_generate_pack(page, shot, seeded):
    _generate(page, seeded)
    res = page.get_by_test_id("evidence-pack-result")
    md_link = page.get_by_test_id("evidence-pack-md-link")
    pdf_link = page.get_by_test_id("evidence-pack-pdf-link")
    expect(md_link).to_be_visible()
    expect(pdf_link).to_be_visible()
    md = page.request.get(md_link.get_attribute("href"))
    pdf = page.request.get(pdf_link.get_attribute("href"))
    assert md.status == 200 and pdf.status == 200
    assert pdf.body()[:4] == b"%PDF"
    heads = [l for l in md.text().splitlines() if l.startswith("#")]
    pos = []
    for pat in SECTIONS:
        idx = next((i for i, h in enumerate(heads) if re.search(pat, h, re.I)), None)
        assert idx is not None, f"no heading for {pat!r} in {heads}"
        pos.append(idx)
    assert pos == sorted(pos), f"sections out of order: {heads}"
    prev = page.get_by_test_id("evidence-pack-preview")
    expect(prev.locator("h1,h2,h3").first).to_be_visible()
    assert prev.locator("h1,h2,h3").count() >= 7
    assert "## " not in prev.inner_text()
    expect(page.get_by_test_id("evidence-pack-bundle")).to_contain_text(re.compile(r"\S"))
    expect(page.get_by_test_id("evidence-pack-bundle")).to_contain_text(re.compile(r"bundle|dataset|[0-9a-f]{6}", re.I))
    shot("then")


def test_ac12_pack_includes_finding(page, shot, seeded):
    _generate(page, seeded)
    mon = _monitoring_text(page.get_by_test_id("evidence-pack-preview"))
    for needle in ("transfer_funds", "list_credentials", "2026-11-07 02:14"):
        assert needle in mon, f"{needle} missing from monitoring section:\n{mon}"
    shot("then")


def test_ac12_fleet_to_pack_end_to_end(page, shot, seeded):
    page.goto("./#/")
    row = page.get_by_test_id("fleet-row").filter(has_text="treasury-sweep-agent")
    expect(row).to_have_count(1)
    expect(row.get_by_test_id("tier-badge")).to_have_text(re.compile(r"Tier 1"))
    expect(row.get_by_test_id("open-findings")).to_have_text("1")
    shot("fleet")
    row.get_by_role("link", name="treasury-sweep-agent").click()
    item = page.get_by_test_id("finding-item")
    expect(item).to_have_count(1)
    expect(item).to_have_attribute("data-finding-type", "scope_drift")
    expect(item).to_have_attribute("data-status", "open")
    shot("agent")
    item.get_by_test_id("finding-timeline-link").click()
    expect(page.get_by_test_id("timeline-page")).to_be_visible()
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()
    shot("timeline")
    page.go_back()
    expect(page.get_by_test_id("evidence-pack-generate")).to_be_visible()
    page.get_by_role("button", name="Generate evidence pack").click()
    expect(page.get_by_test_id("evidence-pack-result")).to_be_visible(timeout=15000)
    mon = _monitoring_text(page.get_by_test_id("evidence-pack-preview"))
    assert "transfer_funds" in mon and "scope_drift" in mon
    shot("then")


def test_pack_preview_has_no_claude(page, shot, seeded):
    _generate(page, seeded)
    txt = page.get_by_test_id("evidence-pack-preview").inner_text()
    assert len(txt) > 200
    assert "claude" not in txt.lower()
    shot("then")
