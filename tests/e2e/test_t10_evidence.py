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

@pytest.fixture(autouse=True)
def _identity(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "risk-officer-1", "X-Domino-User": "risk.officer"})


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


# ---- round 2: rendering contracts (no raw Markdown, identifiers intact, card styling, runs, counts) ----
def _api(page, path):
    r = page.request.get(path)
    assert r.status == 200, f"GET {path} -> {r.status}"
    return r.json()


def _runs(page, seeded):
    data = _api(page, f"api/agents/{seeded['treasury_id']}/runs")
    return data["runs"] if isinstance(data, dict) else data


def _span_count(run):
    for k in ("span_count", "spans_count", "event_count", "events", "spans"):
        if k in run:
            v = run[k]
            return v if isinstance(v, int) else len(v)
    raise AssertionError(f"no span count in run {run}")


def test_preview_lists_render_as_lists(page, shot, seeded):
    _generate(page, seeded)
    prev = page.get_by_test_id("evidence-pack-preview")
    lines = [l.strip() for l in prev.inner_text().splitlines() if l.strip()]
    bad = [l for l in lines if l.startswith("- ") or l.startswith("* ")]
    assert not bad, f"raw list markers visible: {bad}"
    expect(prev.locator("li").first).to_be_visible()
    mon = prev.locator("li").filter(has_text=re.compile("transfer_funds|list_credentials"))
    assert mon.count() >= 1, "finding sub-items are not <li> elements"
    shot("then")


def test_preview_identifiers_not_split(page, shot, seeded):
    agent = _api(page, f"api/agents/{seeded['treasury_id']}")
    scopes = [s for s in (agent.get("data_scopes") or (agent.get("manifest") or {}).get("data_scopes") or []) if "." in s]
    assert scopes, f"no dotted data scope on agent: {agent.get('manifest')}"
    _generate(page, seeded)
    txt = page.get_by_test_id("evidence-pack-preview").inner_text()
    for s in scopes:
        assert s in txt, f"{s!r} not contiguous in preview"
    assert "decision_impact =" not in txt
    assert "` " not in txt and "`" not in txt, "stray backticks in preview"
    shot("then")


def test_evidence_panel_has_card_header(page, shot, seeded):
    page.goto(f"./#/agents/{seeded['treasury_id']}")
    card = page.get_by_test_id("evidence-pack-card")
    expect(card).to_be_visible()
    expect(card.get_by_role("heading", name="Evidence pack")).to_be_visible()
    shot("then")


def test_runs_card_lists_all_runs(page, shot, seeded):
    runs = _runs(page, seeded)
    assert len(runs) >= 2, runs
    page.goto(f"./#/agents/{seeded['treasury_id']}")
    expect(page.get_by_test_id("run-item")).to_have_count(len(runs))
    shot("then")


def test_records_section_event_total_matches_api(page, shot, seeded):
    total = sum(_span_count(r) for r in _runs(page, seeded))
    _generate(page, seeded)
    prev = page.get_by_test_id("evidence-pack-preview")
    txt = prev.inner_text()
    m = re.search(r"records.*", txt[re.search(r"records and retention|records", txt, re.I).start():], re.I | re.S)
    sect = m.group(0)
    nums = [int(n) for n in re.findall(r"\b(\d+)\s+(?:events?|spans?)\b", sect, re.I)]
    assert total in nums, f"API total {total} not in records section counts {nums}:\n{sect[:500]}"
    shot("then")
