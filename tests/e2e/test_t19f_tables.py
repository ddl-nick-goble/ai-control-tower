"""T-19f e2e: table CSS and Agent/Run page design fixes (F-1, F-3 .. F-9; F-2 lives in test_t19a_shell_fleet).

Every check is geometry or computed style on the seeded app (treasury-sweep-agent and its weekend-drift run).
F-6 ratio rule: on the empty agent page at 1440 the two columns (data-testid-less .ag-main / .ag-side wrappers, found
as the Overview card's column and the Findings card's column) must be balanced: the wider is at most 15% wider than
the narrower, OR the main:side width ratio is at most 2:1 (main never more than twice the side).
"""
import re
import pathlib

import pytest
from playwright.sync_api import expect

from test_t19a_shell_fleet import (app, authed, base_url, browser_context_args, demo_url,  # noqa: F401
                                   seeded)
from _waits import settle

CSS = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "design.css"


def _fleet(page):
    page.goto("./#/")
    expect(page.get_by_test_id("fleet-row").first).to_be_visible()


def _agent(page, aid):
    page.goto(f"./#/agents/{aid}")
    expect(page.get_by_role("heading", level=1)).to_be_visible()


def _runs_card(page):
    return page.get_by_role("region", name="Runs", exact=True).or_(
        page.locator("section").filter(has=page.get_by_role("heading", name="Runs", exact=True)))


def _style(loc, prop):
    return loc.evaluate("(e, p) => getComputedStyle(e)[p]", prop)


def _red(color):
    r, g, b = [float(x) for x in re.findall(r"[\d.]+", color)[:3]]
    return r >= 150 and g <= 100 and b <= 100


# ---- F-1 ----------------------------------------------------------------------------------------------------
def test_f1_short_fleet_table_has_no_blank_viewport_under_it(page, shot):
    _fleet(page)
    settle(page)
    rows = page.get_by_test_id("fleet-row")
    card = page.locator(".ds-table-card").filter(has=rows.first)
    wrap = card.locator("xpath=..")
    last = rows.last.bounding_box()
    last_bottom = last["y"] + last["height"]
    cb, wb = card.bounding_box(), wrap.bounding_box()
    assert cb["y"] + cb["height"] - last_bottom <= 24, "table card extends far below the last row"
    assert wb["y"] + wb["height"] - last_bottom <= 64, "table wrapper leaves a blank band under the last row"
    assert wb["height"] < 900 * 0.9, "wrapper is about a viewport tall for a short table"
    shot("then")


# ---- F-3 ----------------------------------------------------------------------------------------------------
def test_f3_th_rules_scoped_to_fleet_table(page, shot, seeded):
    assert "c-name-h" not in CSS.read_text(), "dead .c-name-h rule still in design.css"
    _fleet(page)
    name_th = page.get_by_role("columnheader", name=re.compile("Name", re.I))
    assert name_th.bounding_box()["width"] >= 180
    assert _style(name_th, "boxShadow") != "none", "Fleet th lost its shadow"
    _agent(page, seeded["treasury_id"])
    runs = _runs_card(page).get_by_role("table")
    expect(runs.get_by_test_id("run-item").first).to_be_visible()
    for th in runs.get_by_role("columnheader").all():
        assert _style(th, "boxShadow") == "none", "Runs table th inherits the Fleet shadow"
    second = runs.get_by_role("columnheader").nth(1)
    assert _style(second, "minWidth") in ("0px", "auto"), "Runs 2nd th inherits the Fleet name min-width"
    for table in page.locator("table.ag-table-static").all():
        for th in table.get_by_role("columnheader").all():
            assert _style(th, "boxShadow") == "none"
        assert _style(table.get_by_role("columnheader").nth(1), "minWidth") in ("0px", "auto")
    shot("then")


# ---- F-4 ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("w", [1024, 1440])
def test_f4_spans_text_never_touches_chevron(page, shot, seeded, w):
    page.set_viewport_size({"width": w, "height": 900})
    _agent(page, seeded["treasury_id"])
    rows = _runs_card(page).get_by_test_id("run-item")
    expect(rows.first).to_be_visible()
    for row in rows.all():
        text = row.locator("td").nth(2)
        chev = row.locator("td").nth(3).locator("svg")
        t = text.evaluate("""e => { const r = document.createRange(); r.selectNodeContents(e);
            const b = r.getBoundingClientRect(); return {l: b.left, r: b.right, t: b.top, b: b.bottom}; }""")
        c = chev.bounding_box()
        assert "span" in text.inner_text()
        assert c["x"] - t["r"] >= 8, f"spans text ends {t['r']}, chevron starts {c['x']} at {w}px"
    shot("then")


# ---- F-5 ----------------------------------------------------------------------------------------------------
def test_f5_violation_row_has_red_left_bar(page, shot, seeded):
    _agent(page, seeded["treasury_id"])
    if page.get_by_role("tab", name="Findings", exact=True).count():
        page.get_by_role("tab", name="Findings", exact=True).click()
    page.get_by_test_id("finding-timeline-link").first.click()
    v = page.locator("[data-testid=timeline-event][data-violation=true]").first
    expect(v).to_be_visible()
    settle(page)
    border = v.evaluate("""e => { const c = getComputedStyle(e);
        return [c.borderLeftColor, parseFloat(c.borderLeftWidth), c.borderLeftStyle]; }""")
    before = v.evaluate("""e => { const c = getComputedStyle(e, '::before');
        return [c.backgroundColor, parseFloat(c.width) || 0, c.content]; }""")
    bar_border = _red(border[0]) and border[1] >= 2 and border[2] != "none"
    bar_before = before[2] not in ("none", "normal") and _red(before[0]) and before[1] >= 2
    assert bar_border or bar_before, f"no red left bar: border={border} before={before}"
    shot("then")


# ---- F-6 ----------------------------------------------------------------------------------------------------
def test_f6_empty_agent_page_columns_balanced_at_1440(page, shot, seeded):
    page.set_viewport_size({"width": 1440, "height": 900})
    rid = seeded["recon_id"]
    page.route(re.compile(r"/api/agents/[^/]+/runs$"), lambda r: r.fulfill(json=[]))
    page.route(re.compile(r"/api/agents/[^/]+/findings$"), lambda r: r.fulfill(json=[]))
    _agent(page, rid)
    expect(page.get_by_text(re.compile("No runs", re.I)).first).to_be_visible()
    settle(page)
    main = page.locator("section").filter(has=page.get_by_role("heading", name="Runs", exact=True)) \
        .first.locator("xpath=..").bounding_box()
    side = page.locator("section").filter(has=page.get_by_role("heading", name="Overview", exact=True)) \
        .first.locator("xpath=..").bounding_box()
    big, small = max(main["width"], side["width"]), min(main["width"], side["width"])
    ok = big <= small * 1.15 or main["width"] <= side["width"] * 2
    assert ok, f"unbalanced columns: main {main['width']:.0f}px vs side {side['width']:.0f}px"
    shot("then")


# ---- F-7 ----------------------------------------------------------------------------------------------------
def test_f7_runs_thead_fill_uniform(page, shot, seeded):
    _agent(page, seeded["treasury_id"])
    runs = _runs_card(page).get_by_role("table")
    expect(runs.get_by_test_id("run-item").first).to_be_visible()
    ths = runs.locator("th").all()
    assert len(ths) >= 4
    fills = [_style(th, "backgroundColor") for th in ths]
    images = [_style(th, "backgroundImage") for th in ths]
    assert len(set(fills)) == 1 and fills[0] != "rgba(0, 0, 0, 0)", fills
    assert len(set(images)) == 1, images
    shot("then")


# ---- F-8 ----------------------------------------------------------------------------------------------------
def _is_ghost(btn):
    return btn.evaluate("""e => { const c = getComputedStyle(e);
        const clear = x => x === 'transparent' || /rgba\\(.*,\\s*0\\)$/.test(x);
        return e.classList.contains('ds-btn-ghost') && clear(c.backgroundColor) && clear(c.borderTopColor); }""")


def test_f8_card_level_try_again_is_ghost_button(page, shot, seeded):
    page.route(re.compile(r"/api/agents/[^/]+/runs$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    _agent(page, seeded["treasury_id"])
    card = _runs_card(page)
    btn = card.get_by_role("button", name="Try again")
    expect(btn).to_be_visible()
    assert _is_ghost(btn), "inline card Try again is not a ghost button"
    page.unroute(re.compile(r"/api/agents/[^/]+/runs$"))
    page.route(re.compile(r"/api/agents/[^/]+$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.goto(f"./#/agents/{seeded['recon_id']}")
    expect(page.get_by_role("alert").first).to_be_visible()
    btn = page.get_by_role("alert").get_by_role("button", name="Try again")
    expect(btn).to_be_visible()
    assert _is_ghost(btn), "error card Try again is a bordered default button, not ghost"
    shot("then")


# ---- F-9 ----------------------------------------------------------------------------------------------------
def test_f9_filter_chips_and_copy_buttons_show_focus_ring_on_keyboard(page, shot, seeded):
    page.goto(f"./#/runs/{seeded['run_id']}")
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()
    seen = {"chip": 0, "copy": 0}
    bad = []
    for _ in range(80):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => { const e = document.activeElement; if (!e || e === document.body) return null;
            const grp = e.closest('[role=group]');
            const kind = e.matches('[data-testid=copy-button]') ? 'copy'
                : (grp && grp.getAttribute('aria-label') === 'Filter by event type' ? 'chip' : null);
            if (!kind) return null;
            const s = getComputedStyle(e);
            const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) ||
                (s.boxShadow && s.boxShadow !== 'none');
            return {kind, ring, fv: e.matches(':focus-visible'), name: e.textContent.trim() || e.getAttribute('aria-label')}; }""")
        if info:
            seen[info["kind"]] += 1
            if not (info["ring"] and info["fv"]):
                bad.append(info)
        if seen["chip"] >= 5 and seen["copy"] >= 1:
            break
    assert seen["chip"] >= 5 and seen["copy"] >= 1, seen
    assert not bad, f"controls without a visible keyboard focus ring: {bad}"
    shot("then")
