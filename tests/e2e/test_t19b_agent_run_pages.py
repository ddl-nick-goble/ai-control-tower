"""T-19b e2e: Agent page (S-3), Run timeline (S-4), S-5 quality bar, S-6 regressions, T-19a carry-overs.

Own seeded app on 9891 and demo app on 9892 (shared fixtures from the T-19a module); screenshots in /mnt/data/ai-control-tower/qa/T-19b/.
New data-testid contract (role/label/text locators are primary):
  Agent page
    agent-header        header band; contains h1 (agent name), agent-source chip, tier-badge, agent-status pill
    agent-source        source chip in the header
    agent-actions       group of primary actions, right of the title: button "Approve" (when approvable),
                        button "Generate evidence pack", demo buttons ("Approve as demo approver", "Run recon agent") in demo mode
    agent-details       the <dl> definition list (dt/dd) in the Overview card
    copy-button         every "copy" affordance (role=button, accessible name starting "Copy"); copies the FULL value
    id-value            truncated long id/ARN: element with title=<full value>, single line (nowrap + ellipsis)
    Sections: cards (<section> with heading) or tabs (role=tab) titled Overview, Tier and criteria, Findings, Runs,
              Dependencies, Governance, Approvals, Validation, Evidence pack
    finding-item children: finding-severity, finding-reason, finding-time, finding-timeline-link (existing)
    run-item: text shows span count with thousands separators
  Run page
    run-header          header: run-id (title=full id, truncated) + copy-button, link "Agent" ->  agent, run-range, run-event-count
    run-id, run-range, run-event-count
    timeline-filter     role=group name "Filter by event type": buttons (aria-pressed) All, Input, Model call, Tool call, Violation
    timeline-event      (existing) + children timeline-event-type (label text), timeline-event-time,
                        timeline-payload (the <pre>; collapsed when > 6 lines, data-collapsed="true"),
                        payload-toggle (button, aria-expanded, "Show more"/"Show less"), copy-button (copies the payload)
    Violation events: data-violation="true" (existing) + a visible "Violation" label; finding-timeline-link lands on the run
    with that event scrolled into view.
  States: loading = role=status text "Loading ..." ; error = role=alert + button "Try again"; run empty = text "No events"
"""
import json
import os
import re

import pytest
from playwright.sync_api import expect

from test_t19a_shell_fleet import (PORT, DEMO_PORT, _start, _stop, app, authed, base_url,  # noqa: F401
                                   browser_context_args, demo_url, seeded)

SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-19b"


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


def _agent(page, aid):
    page.goto(f"./#/agents/{aid}")
    expect(page.get_by_role("heading", level=1)).to_be_visible()


def _run(page, rid):
    page.goto(f"./#/runs/{rid}")
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()


def _no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _section(page, title):
    """A section is a heading (card) or a tab."""
    h = page.get_by_role("heading", name=title, exact=True)
    t = page.get_by_role("tab", name=title, exact=True)
    return h.or_(t)


def _timeline_json(n, long_payload=False, agent_id="x"):
    ev = []
    for i in range(n):
        kind = "tool_call" if long_payload and i == 1 else ("input" if i % 3 == 0 else "model_call")
        e = {"kind": kind, "ts": f"2026-11-07T02:{(i // 60) % 60:02d}:{i % 60:02d}.000000Z", "span_id": f"s{i:04d}",
             "name": f"step {i}", "attrs": {}}
        if kind == "tool_call":
            e["args_redacted"] = json.dumps({f"field_{k}": "value " * 4 for k in range(40)}, indent=2)
        if kind == "input":
            e["content"] = "hello"
        ev.append(e)
    return {"run_id": "mockrun", "agent_id": agent_id, "started_at": "2026-11-07T02:00:00.000000Z",
            "ended_at": "2026-11-07T03:00:00.000000Z", "events": ev}


# ------------------------------------------------------------------ S-3 Agent page
def test_s3_header_name_chip_tier_status_actions_right(page, shot, seeded):
    _agent(page, seeded["treasury_id"])
    head = page.get_by_test_id("agent-header")
    expect(head).to_be_visible()
    expect(head.get_by_role("heading", level=1, name="treasury-sweep-agent")).to_be_visible()
    expect(head.get_by_test_id("agent-source")).to_be_visible()
    expect(head.get_by_test_id("tier-badge")).to_be_visible()
    expect(head.get_by_test_id("agent-status")).to_be_visible()
    actions = page.get_by_test_id("agent-actions")
    expect(actions).to_be_visible()
    expect(actions.get_by_role("button", name="Generate evidence pack")).to_be_visible()
    h1 = head.get_by_role("heading", level=1).bounding_box()
    a = actions.bounding_box()
    assert a["x"] > h1["x"] + 400, "primary actions should be grouped on the right of the header"
    assert abs((a["y"] + a["height"] / 2) - (h1["y"] + h1["height"] / 2)) < 60
    shot("then")


def test_s3_approve_in_header_actions(page, context, shot, seeded):
    context.set_extra_http_headers({"X-Domino-User-Id": "approver-2", "X-Domino-User": "approver-2"})
    _agent(page, seeded["approvable_id"])
    page.get_by_test_id("agent-actions").get_by_role("button", name="Approve", exact=True).click()
    expect(page.get_by_test_id("approve-success")).to_contain_text("Approved")
    expect(page.get_by_test_id("agent-status")).to_have_text(re.compile("^approved$", re.I))
    shot("then")


@pytest.mark.parametrize("title", ["Overview", "Tier and criteria", "Findings", "Runs", "Dependencies",
                                   "Governance", "Approvals", "Validation", "Evidence pack"])
def test_s3_titled_sections(page, shot, seeded, title):
    _agent(page, seeded["recon_id"])
    expect(_section(page, title).first).to_be_visible()
    shot("then")


def test_s3_no_long_undifferentiated_scroll(page, shot, seeded):
    """Cards: each titled section is a distinct surface. Tabs: only one panel is shown at a time."""
    _agent(page, seeded["recon_id"])
    if page.get_by_role("tab").count():
        expect(page.get_by_role("tabpanel")).to_have_count(1)
    else:
        for title in ("Findings", "Runs", "Governance"):
            sec = page.get_by_role("heading", name=title, exact=True).first.locator("xpath=ancestor::section[1]")
            expect(sec).to_have_count(1)
            assert sec.evaluate("""e => { const s = getComputedStyle(e);
                return s.borderTopWidth !== '0px' || s.boxShadow !== 'none' || s.backgroundColor !== 'rgba(0, 0, 0, 0)'; }""")
    shot("then")


def test_s3_findings_severity_reason_time_and_timeline_link(page, shot, seeded):
    _agent(page, seeded["treasury_id"])
    if page.get_by_role("tab", name="Findings", exact=True).count():
        page.get_by_role("tab", name="Findings", exact=True).click()
    item = page.get_by_test_id("finding-item").first
    expect(item).to_be_visible()
    for tid in ("finding-severity", "finding-reason", "finding-time"):
        expect(item.get_by_test_id(tid)).to_be_visible()
        assert item.get_by_test_id(tid).inner_text().strip()
    assert re.search(r"20\d\d", item.get_by_test_id("finding-time").inner_text())
    link = item.get_by_test_id("finding-timeline-link")
    expect(link).to_be_visible()
    link.click()
    expect(page).to_have_url(re.compile(rf"#/runs/{seeded['run_id']}"))
    shot("then")


def test_s3_definition_list_and_truncated_ids_with_copy(page, context, shot, seeded):
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    _agent(page, seeded["recon_id"])
    dl = page.get_by_test_id("agent-details")
    expect(dl).to_be_visible()
    assert dl.evaluate("e => e.tagName") == "DL"
    assert dl.locator("dt").count() >= 3 and dl.locator("dt").count() == dl.locator("dd").count()
    ids = page.get_by_test_id("id-value")
    assert ids.count() >= 1
    first = ids.first
    full = first.get_attribute("title")
    assert full and len(full) > 8
    assert first.evaluate("e => getComputedStyle(e).whiteSpace") == "nowrap"
    assert first.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
    box = first.bounding_box()
    assert box["height"] < 40, "id wrapped over several lines"
    copy = first.locator("xpath=..").get_by_test_id("copy-button").or_(first.locator("xpath=following-sibling::*[@data-testid='copy-button']")).first
    copy.click()
    assert page.evaluate("navigator.clipboard.readText()") == full
    shot("then")


def test_s3_demo_actions_in_header(page, shot, demo_url):
    page.goto(demo_url + "#/")
    page.get_by_test_id("fleet-row").filter(has_text="recon-agent").get_by_role("link", name="recon-agent").click()
    actions = page.get_by_test_id("agent-actions")
    expect(actions.get_by_role("button", name="Run recon agent")).to_be_visible()
    shot("then")


def test_s3_run_span_count_thousands_separator(page, shot, seeded):
    rid = "r" * 32
    page.route(re.compile(r"/api/agents/[^/]+/runs$"), lambda r: r.fulfill(
        json=[{"run_id": rid, "started_at": "2026-11-07T02:00:00Z", "span_count": 12345}]))
    _agent(page, seeded["recon_id"])
    if page.get_by_role("tab", name="Runs", exact=True).count():
        page.get_by_role("tab", name="Runs", exact=True).click()
    expect(page.get_by_test_id("run-item").first).to_contain_text("12,345")
    shot("then")


# ------------------------------------------------------------------ S-4 Run timeline
def test_s4_header_run_id_copy_agent_link_range_count(page, context, shot, seeded):
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    _run(page, seeded["run_id"])
    head = page.get_by_test_id("run-header")
    expect(head).to_be_visible()
    rid = head.get_by_test_id("run-id")
    expect(rid).to_have_attribute("title", seeded["run_id"])
    assert rid.evaluate("e => getComputedStyle(e).whiteSpace") == "nowrap"
    assert rid.bounding_box()["height"] < 40
    head.get_by_test_id("copy-button").first.click()
    assert page.evaluate("navigator.clipboard.readText()") == seeded["run_id"]
    expect(head.get_by_role("link", name=re.compile("treasury-sweep-agent|Agent"))).to_be_visible()
    expect(head.get_by_test_id("run-range")).to_contain_text(re.compile(r"20\d\d-\d\d-\d\d"))
    n = page.get_by_test_id("timeline-event").count()
    expect(head.get_by_test_id("run-event-count")).to_contain_text(re.compile(rf"\b{n}\b"))
    shot("then")


def test_s4_event_type_labels_and_aligned_timestamps(page, shot, seeded):
    _run(page, seeded["run_id"])
    evs = page.get_by_test_id("timeline-event")
    kinds = {e.get_attribute("data-kind") for e in evs.all()}
    assert {"tool_call"} <= kinds
    xs = set()
    for e in evs.all():
        label = e.get_by_test_id("timeline-event-type")
        expect(label).to_be_visible()
        assert label.inner_text().strip()
        xs.add(round(e.get_by_test_id("timeline-event-time").bounding_box()["x"]))
    assert len(xs) == 1, f"timestamps not aligned: {xs}"
    assert page.get_by_test_id("timeline-event").filter(has=page.get_by_test_id("timeline-event-type").filter(has_text="Tool call")).count() >= 1
    assert evs.first.locator("svg").count() >= 1, "event-type icon missing"
    shot("then")


def test_s4_violation_distinct_and_linked_from_finding(page, shot, seeded):
    _agent(page, seeded["treasury_id"])
    if page.get_by_role("tab", name="Findings", exact=True).count():
        page.get_by_role("tab", name="Findings", exact=True).click()
    page.get_by_test_id("finding-timeline-link").first.click()
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()
    v = page.locator("[data-testid=timeline-event][data-violation=true]")
    assert v.count() >= 1
    expect(v.first).to_contain_text("Violation")
    expect(v.first).to_be_in_viewport()
    other = page.locator("[data-testid=timeline-event][data-violation=false]").first
    sv = lambda l: l.evaluate("e => { const c = getComputedStyle(e); return [c.backgroundColor, c.borderLeftColor, c.borderLeftWidth].join('|'); }")
    assert sv(v.first) != sv(other)
    shot("then")


def test_s4_filter_by_event_type(page, shot, seeded):
    _run(page, seeded["run_id"])
    f = page.get_by_role("group", name="Filter by event type")
    expect(f).to_be_visible()
    for name in ("All", "Input", "Model call", "Tool call", "Violation"):
        expect(f.get_by_role("button", name=name, exact=True)).to_be_visible()
    evs = page.get_by_test_id("timeline-event")
    total = evs.count()
    f.get_by_role("button", name="Tool call", exact=True).click()
    expect(f.get_by_role("button", name="Tool call", exact=True)).to_have_attribute("aria-pressed", "true")
    n = evs.count()
    assert 0 < n < total
    for e in evs.all():
        assert e.get_attribute("data-kind") == "tool_call"
    f.get_by_role("button", name="Violation", exact=True).click()
    for e in evs.all():
        assert e.get_attribute("data-violation") == "true"
    f.get_by_role("button", name="All", exact=True).click()
    expect(evs).to_have_count(total)
    shot("then")


def test_s4_payloads_collapsible_monospace_copy(page, context, shot, seeded):
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    tl = _timeline_json(5, long_payload=True, agent_id=seeded["recon_id"])
    page.route(re.compile(r"/api/runs/mockrun/timeline$"), lambda r: r.fulfill(json=tl))
    page.goto("./#/runs/mockrun")
    ev = page.locator("[data-testid=timeline-event][data-kind=tool_call]").first
    expect(ev).to_be_visible()
    pay = ev.get_by_test_id("timeline-payload")
    expect(pay).to_have_attribute("data-collapsed", "true")
    assert "mono" in pay.evaluate("e => getComputedStyle(e).fontFamily").lower() or \
        "courier" in pay.evaluate("e => getComputedStyle(e).fontFamily").lower()
    collapsed_h = pay.bounding_box()["height"]
    assert collapsed_h < 200
    tog = ev.get_by_test_id("payload-toggle")
    expect(tog).to_have_attribute("aria-expanded", "false")
    tog.click()
    expect(tog).to_have_attribute("aria-expanded", "true")
    assert pay.bounding_box()["height"] > collapsed_h * 1.5
    ev.get_by_test_id("copy-button").click()
    assert "field_39" in page.evaluate("navigator.clipboard.readText()")
    shot("then")


def test_s4_event_count_thousands_separator(page, shot, seeded):
    tl = _timeline_json(1200, agent_id=seeded["recon_id"])
    page.route(re.compile(r"/api/runs/mockrun/timeline$"), lambda r: r.fulfill(json=tl))
    page.goto("./#/runs/mockrun")
    expect(page.get_by_test_id("run-event-count")).to_contain_text("1,200")
    shot("then")


# ------------------------------------------------------------------ S-5 quality bar
CONTRAST_JS = """() => {
  const lum = (r,g,b) => { const f = c => { c/=255; return c<=0.03928? c/12.92 : Math.pow((c+0.055)/1.055,2.4) }; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b) };
  const parse = s => { const m = s.match(/rgba?\\(([^)]+)\\)/); if(!m) return null; const p = m[1].split(',').map(x=>parseFloat(x)); return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1} };
  const bgOf = e => { while (e) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.95) return c; e = e.parentElement } return {r:255,g:255,b:255} };
  const bad = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) {
    if (!n.textContent.trim()) continue;
    const e = n.parentElement; const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.display === 'none' || e.closest('[hidden]') || e.closest('script,style')) continue;
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const fg = parse(cs.color), bg = bgOf(e); if (!fg) continue;
    const l1 = lum(fg.r,fg.g,fg.b), l2 = lum(bg.r,bg.g,bg.b);
    const ratio = (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    if (ratio < need) bad.push(e.tagName + ':' + n.textContent.trim().slice(0,30) + ':' + ratio.toFixed(2));
  }
  return bad;
}"""


@pytest.mark.parametrize("which", ["agent", "run"])
@pytest.mark.parametrize("w,h", [(1440, 900), (1024, 768)])
def test_s5_no_horizontal_scroll_and_contrast(page, shot, seeded, which, w, h):
    page.set_viewport_size({"width": w, "height": h})
    if which == "agent":
        _agent(page, seeded["treasury_id"])
    else:
        _run(page, seeded["run_id"])
    page.wait_for_timeout(300)
    assert _no_hscroll(page), f"horizontal scroll at {w}"
    bad = page.evaluate(CONTRAST_JS)
    assert not bad, f"AA contrast failures: {bad[:10]}"
    shot(f"then-{w}")


@pytest.mark.parametrize("which", ["agent", "run"])
def test_s5_keyboard_focus_visible(page, shot, seeded, which):
    _agent(page, seeded["treasury_id"]) if which == "agent" else _run(page, seeded["run_id"])
    seen = 0
    for _ in range(14):
        page.keyboard.press("Tab")
        ok = page.evaluate("""() => { const e = document.activeElement; if (!e || e === document.body) return null;
            const s = getComputedStyle(e);
            return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none'); }""")
        if ok is None:
            continue
        seen += 1
        assert ok, "focused control has no visible focus indicator"
    assert seen >= 5
    shot("then")


@pytest.mark.parametrize("which", ["agent", "run"])
def test_s5_ui_never_mentions_claude(page, shot, seeded, which):
    _agent(page, seeded["recon_id"]) if which == "agent" else _run(page, seeded["run_id"])
    page.wait_for_timeout(500)
    assert not re.search(r"claude|anthropic", page.locator("body").inner_text(), re.I)
    assert not re.search(r"claude|anthropic", page.content(), re.I)
    shot("then")


def test_s5_loading_state_agent(page, shot, seeded):
    def slow(route):
        page.wait_for_timeout(1500)
        route.continue_()
    page.route(re.compile(r"/api/agents/[^/]+$"), slow)
    page.goto(f"./#/agents/{seeded['recon_id']}")
    st = page.get_by_role("status").filter(has_text=re.compile("loading", re.I))
    expect(st.first).to_be_visible()
    assert page.locator("body").inner_text().strip() != ""
    shot("then")


def test_s5_error_state_agent_and_run(page, shot, seeded):
    page.route(re.compile(r"/api/agents/[^/]+$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.goto(f"./#/agents/{seeded['recon_id']}")
    expect(page.get_by_role("alert").first).to_be_visible()
    expect(page.get_by_role("button", name="Try again")).to_be_visible()
    page.unroute(re.compile(r"/api/agents/[^/]+$"))
    page.route(re.compile(r"/api/runs/[^/]+/timeline$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.goto("./#/runs/" + seeded["run_id"])
    expect(page.get_by_role("alert").first).to_be_visible()
    expect(page.get_by_role("button", name="Try again")).to_be_visible()
    shot("then")


def test_s5_empty_states(page, shot, seeded):
    page.route(re.compile(r"/api/runs/mockrun/timeline$"), lambda r: r.fulfill(json=_timeline_json(0, agent_id=seeded["recon_id"])))
    page.goto("./#/runs/mockrun")
    expect(page.get_by_text(re.compile("No events", re.I)).first).to_be_visible()
    page.route(re.compile(r"/api/agents/[^/]+/runs$"), lambda r: r.fulfill(json=[]))
    _agent(page, seeded["recon_id"])
    if page.get_by_role("tab", name="Runs", exact=True).count():
        page.get_by_role("tab", name="Runs", exact=True).click()
    expect(page.get_by_text(re.compile("No runs", re.I)).first).to_be_visible()
    shot("then")


# ------------------------------------------------------------------ S-6 regressions
def test_s6_existing_testids_on_agent_and_run(page, shot, seeded):
    _agent(page, seeded["recon_id"])
    for tid in ("tier-card", "agent-status", "approvals-panel", "validation-card", "evidence-pack-card", "tier-badge",
                "criteria-item", "tier-reasoning"):
        expect(page.get_by_test_id(tid).first).to_be_attached()
    _agent(page, seeded["treasury_id"])
    expect(page.get_by_test_id("finding-item").first).to_be_attached()
    _run(page, seeded["run_id"])
    expect(page.get_by_test_id("timeline-page")).to_be_attached()
    expect(page.get_by_test_id("timeline-event-args").first).to_be_attached()
    shot("then")


def test_s6_evidence_pack_generates(page, shot, seeded):
    _agent(page, seeded["recon_id"])
    page.get_by_test_id("agent-actions").get_by_role("button", name="Generate evidence pack").click()
    expect(page.get_by_test_id("evidence-pack-result")).to_be_visible(timeout=60000)
    shot("then")


# ------------------------------------------------------------------ carry-overs (Fleet / banner)
def _mock_fleet(page, base_rows, n=60):
    rows = []
    for i in range(n):
        r = dict(base_rows[i % len(base_rows)])
        r["id"] = f"mock-{i:03d}"
        r["name"] = f"mock-agent-{i:03d}"
        rows.append(r)
    rows[0]["name"] = "an-extremely-long-agent-name-that-will-certainly-be-truncated-in-the-fleet-table-column-0001"
    page.route(re.compile(r"/api/agents(\?.*)?$"), lambda r: r.fulfill(json=rows))
    return rows


def test_co_fleet_header_pinned_and_name_tooltip(page, shot, seeded):
    base = page.request.get("./api/agents").json()
    rows = _mock_fleet(page, base)
    page.goto("./#/")
    expect(page.get_by_test_id("fleet-row")).to_have_count(60)
    long_name = rows[0]["name"]
    first = page.get_by_test_id("fleet-row").first
    expect(first.get_by_title(long_name).first).to_be_attached()
    last = page.get_by_test_id("fleet-row").last
    last.scroll_into_view_if_needed()
    page.mouse.wheel(0, 3000)
    page.wait_for_timeout(300)
    th = page.get_by_role("columnheader", name=re.compile("Name", re.I))
    expect(th).to_be_in_viewport()
    assert 0 <= th.bounding_box()["y"] < 140, "table header not pinned while scrolling"
    expect(last).to_be_in_viewport()
    shot("then")


def test_co_demo_banner_not_repeating_text(page, shot, demo_url):
    page.goto(demo_url + "#/")
    banner = page.get_by_test_id("demo-banner")
    expect(banner).to_be_visible()
    toggle = banner.get_by_role("button", name=re.compile("stand-ins"))
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    text = re.sub(r"\s+", " ", banner.inner_text())
    assert len(re.findall(r"Demo mode: simulated data", text)) == 1, text
    shot("then")


@pytest.mark.parametrize("key,hint", [("total", "Whole fleet"), ("tier1", "Highest consequence"),
                                      ("pending-review", "Awaiting approval")])
def test_co_tile_hint_exposed_to_screen_readers(page, shot, seeded, key, hint):
    page.goto("./#/")
    expect(page.get_by_test_id("fleet-row").first).to_be_visible()
    tile = page.get_by_test_id(f"fleet-tile-{key}")
    named = page.get_by_test_id(f"fleet-tile-{key}").and_(page.get_by_role("button", name=re.compile(hint)))
    if named.count() == 0:
        expect(tile).to_have_accessible_description(re.compile(hint))
    shot("then")
