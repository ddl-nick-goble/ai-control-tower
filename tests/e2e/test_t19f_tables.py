"""T-19f e2e: table CSS and Agent/Run page design fixes (F-1, F-3 .. F-9; F-2 lives in test_t19a_shell_fleet).

Every check is geometry or computed style on the seeded app (treasury-sweep-agent and its weekend-drift run).
F-6: columns are found as the Overview card's and Runs card's parents; see F-6 tests below (side 380 +-4, empty text <= 560px, Evidence pack full-width row, bottoms within 40px).
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
_BAR_JS = """e => { const c = getComputedStyle(e), b = getComputedStyle(e, '::before');
    return {shadow: c.boxShadow, outline: c.outlineStyle, outlineOffset: parseFloat(c.outlineOffset) || 0,
            beforeBg: b.backgroundColor, beforeW: parseFloat(b.width) || 0, beforeLeft: b.left, beforeContent: b.content,
            beforePos: b.position,
            bottom: [c.borderBottomColor, parseFloat(c.borderBottomWidth)],
            bodyBottom: (() => { const k = e.querySelector('.rt-body'); if (!k) return null;
                const d = getComputedStyle(k); return [d.borderBottomColor, parseFloat(d.borderBottomWidth)]; })()}; }"""


def _inset_left_red_bar(shadow):
    """True if an inset box-shadow has a red-ish colour and an x offset/spread >= 3px on the left."""
    for part in re.split(r",\s*(?=rgba?\()", shadow or ""):
        m = re.match(r"(rgba?\([^)]*\))\s*(.*)", part.strip())
        if not m or "inset" not in m.group(2) or not _red(m.group(1)):
            continue
        nums = [float(x) for x in re.findall(r"-?[\d.]+(?=px)", m.group(2))]
        if len(nums) >= 1 and nums[0] >= 3:
            return True
    return False


def test_f5_violation_row_has_red_left_bar(page, shot, seeded):
    _agent(page, seeded["treasury_id"])
    if page.get_by_role("tab", name="Findings", exact=True).count():
        page.get_by_role("tab", name="Findings", exact=True).click()
    page.get_by_test_id("finding-timeline-link").first.click()
    rows = page.locator("[data-testid=timeline-event][data-violation=true]")
    hl = rows.filter(has_text="transfer_funds").filter(has_text="02:14").first
    expect(hl).to_be_visible()
    settle(page)
    d = hl.evaluate(_BAR_JS)
    inset = _inset_left_red_bar(d["shadow"])
    before = (d["beforeContent"] not in ("none", "normal") and _red(d["beforeBg"]) and d["beforeW"] >= 3
              and d["beforeLeft"] in ("0px", "auto") and d["beforePos"] == "absolute")
    assert inset or before, f"no red left bar drawn inside the highlighted row (inset shadow or ::before): {d}"
    if d["outline"] != "none":
        assert d["outlineOffset"] <= 0, f"row outline sits outside the row and can cover the bar: {d}"
    other = rows.filter(has_text="list_credentials").first
    expect(other).to_be_visible()
    o = other.evaluate(_BAR_JS)
    for name, (color, width) in (("row", o["bottom"]), ("body", o["bodyBottom"] or ("rgb(255,255,255)", 0))):
        assert width == 0 or not _red(color), f"list_credentials {name} has a red bottom border: {color} {width}px"
    shot("then")


# ---- F-6 ----------------------------------------------------------------------------------------------------
def _col(page, heading):
    card = page.locator("section").filter(has=page.get_by_role("heading", name=heading, exact=True)).first
    return card.evaluate("e => { const c = e.closest('.ag-main, .ag-side'); const b = c.getBoundingClientRect();"
                         " return {x: b.x, y: b.y + scrollY, width: b.width, height: b.height}; }")


def _assert_side_380(page, label):
    main, side = _col(page, "Runs"), _col(page, "Overview")
    assert abs(side["width"] - 380) <= 4, f"{label}: side column is {side['width']:.0f}px, want 380 +-4"
    assert main["width"] > side["width"], f"{label}: main column should take the rest ({main['width']:.0f}px)"
    return main, side


@pytest.mark.parametrize("w", [1280, 1440])
def test_f6_empty_agent_page_columns_balanced(page, shot, seeded, w):
    page.set_viewport_size({"width": w, "height": 900})
    page.route(re.compile(r"/api/agents/[^/]+/runs$"), lambda r: r.fulfill(json=[]))
    page.route(re.compile(r"/api/agents/[^/]+/findings$"), lambda r: r.fulfill(json=[]))
    _agent(page, seeded["recon_id"])
    empty = page.get_by_text(re.compile("No runs", re.I)).first
    expect(empty).to_be_visible()
    settle(page)
    main, side = _assert_side_380(page, f"empty page at {w}")
    cont = page.locator(".ds-container").first.evaluate(
        "e => { const c = getComputedStyle(e), r = e.getBoundingClientRect();"
        " return {w: r.width - parseFloat(c.paddingLeft) - parseFloat(c.paddingRight)}; }")
    gap = side["x"] - (main["x"] + main["width"])
    assert main["width"] >= cont["w"] - 380 - gap - 8, \
        f"main column does not take the rest: {main['width']:.0f}px (container {cont['w']:.0f}, gap {gap:.0f})"
    for t in page.locator(".ag-main .ag-empty p").all():
        tw = t.evaluate("e => e.getBoundingClientRect().width")
        assert tw <= 560, f"empty-state text is {tw:.0f}px wide (> 560px, about 56ch)"
    ev = page.get_by_test_id("evidence-pack-card")
    expect(ev).to_be_visible()
    eb = ev.evaluate("e => { const b = e.getBoundingClientRect(); return {y: b.y + scrollY, width: b.width}; }")
    assert eb["y"] >= max(main["y"] + main["height"], side["y"] + side["height"]) - 1, \
        "Evidence pack card is inside the two-column grid, not a full-width row below it"
    assert eb["width"] >= main["width"] + side["width"] - 4, f"Evidence pack is not full width: {eb['width']:.0f}px"
    bgap = abs((main["y"] + main["height"]) - (side["y"] + side["height"]))
    assert bgap <= 40, f"column bottoms differ by {bgap:.0f}px"
    shot("then")


@pytest.mark.parametrize("w", [1280, 1440])
def test_f6_populated_agent_page_side_column_is_380(page, shot, seeded, w):
    page.set_viewport_size({"width": w, "height": 900})
    _agent(page, seeded["treasury_id"])
    settle(page)
    _assert_side_380(page, f"populated page at {w}")
    ev = page.get_by_test_id("evidence-pack-card").evaluate("e => ({y: e.getBoundingClientRect().y + scrollY})")
    main, side = _col(page, "Runs"), _col(page, "Overview")
    assert ev["y"] >= max(main["y"] + main["height"], side["y"] + side["height"]) - 1, \
        "Evidence pack card is inside the two-column grid"
    shot("then")


@pytest.mark.parametrize("w", [1280, 1440])
def test_f6_side_column_380_when_one_main_card_empty(page, shot, seeded, w):
    page.set_viewport_size({"width": w, "height": 900})
    page.route(re.compile(r"/api/agents/[^/]+/findings$"), lambda r: r.fulfill(json=[]))
    _agent(page, seeded["treasury_id"])
    settle(page)
    _assert_side_380(page, f"one empty main card at {w}")
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
_BTN_JS = """e => { const c = getComputedStyle(e);
    return {bg: c.backgroundColor, bw: parseFloat(c.borderTopWidth), bs: c.borderTopStyle, bc: c.borderTopColor,
            cls: e.classList.contains('ds-btn-ghost')}; }"""


def _assert_secondary(btn, ref, what):
    b, r = btn.evaluate(_BTN_JS), ref.evaluate(_BTN_JS)
    nums = [float(x) for x in re.findall(r"[\d.]+", b["bg"])[:3]]
    assert not b["cls"], f"{what} Try again is still the ghost button"
    assert min(nums) >= 245 and not b["bg"].endswith(", 0)"), f"{what} Try again fill is not white-ish: {b}"
    assert b["bw"] >= 1 and b["bs"] == "solid", f"{what} Try again has no visible 1px border: {b}"
    assert (b["bg"], b["bw"], b["bc"]) == (r["bg"], r["bw"], r["bc"]), f"{what} differs from the secondary button: {b} vs {r}"


def test_f8_try_again_is_secondary_button(page, shot, seeded):
    _agent(page, seeded["treasury_id"])
    ref = page.get_by_test_id("evidence-pack-generate")
    expect(ref).to_be_visible()
    page.route(re.compile(r"/api/agents/[^/]+/runs$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.reload()
    ref = page.get_by_test_id("evidence-pack-generate")
    btn = _runs_card(page).get_by_role("button", name="Try again")
    expect(btn).to_be_visible()
    _assert_secondary(btn, ref, "card-level")
    page.unroute(re.compile(r"/api/agents/[^/]+/runs$"))
    page.route(re.compile(r"/api/agents/[^/]+$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.goto(f"./#/agents/{seeded['recon_id']}")
    expect(page.get_by_role("alert").first).to_be_visible()
    page_btn = page.get_by_role("alert").get_by_role("button", name="Try again")
    expect(page_btn).to_be_visible()
    # the Generate button is not on the error page: probe a plain .ds-btn (the secondary style) in the same page
    page.evaluate("() => { const b = document.createElement('button'); b.className = 'ds-btn'; b.id = 'probe-secondary'; document.body.appendChild(b); }")
    _assert_secondary(page_btn, page.locator("#probe-secondary"), "page-level")
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
            if (e.closest('.ds-tile')) return null;
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
