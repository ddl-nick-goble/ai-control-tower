"""T-20 e2e: navigation and routes (N-1..N-5).

data-testid contract: nav-home, nav-fleet, nav-findings, nav-policies, nav-connections, nav-audit, nav-settings-icon,
page-title, empty-state, not-found. Role/name locators are primary.
"""
import re

import pytest
from playwright.sync_api import expect

from _waits import settle

ORDER = ["Home", "Fleet", "Findings", "Policies", "Connections", "Audit"]
PLACEHOLDERS = ["home", "findings", "policies", "audit"]  # Connections is the PO-5 gallery, not a placeholder


@pytest.fixture(scope="module")
def app(app_session):
    return app_session


def _nav(page):
    return page.get_by_role("navigation", name="Primary")


def _no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _ring(loc):
    return loc.evaluate("""e => { const s = getComputedStyle(e);
        return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none'); }""")


# ---------------------------------------------------------------- N-1
@pytest.mark.smoke
def test_n1_nav_order_and_gear(page, shot, app):
    page.goto("./#/")
    nav = _nav(page)
    for key in ORDER:
        expect(page.get_by_test_id(f"nav-{key.lower()}")).to_be_visible()
    links = nav.get_by_role("link")
    expect(links).to_have_count(7)
    names = [t.strip() for t in links.all_inner_texts()]
    assert names[:6] == ORDER, names
    assert names[-1].startswith("Settings"), names
    expect(nav.get_by_role("link", name="Settings").get_by_test_id("nav-settings-icon")).to_be_visible()
    shot("then")


def test_n1_current_page_and_indicator(page, shot):
    page.goto("./#/")
    fleet, pol = page.get_by_test_id("nav-fleet"), page.get_by_test_id("nav-policies")
    expect(fleet).to_have_attribute("aria-current", "page")
    pol.click()
    expect(page).to_have_url(re.compile(r"#/policies"))
    expect(pol).to_have_attribute("aria-current", "page")
    expect(fleet).not_to_have_attribute("aria-current", "page")
    # visible indicator: the current item is styled differently from a non-current one
    style = "e => { const s = getComputedStyle(e); return [s.color, s.backgroundColor, s.fontWeight, s.borderBottomColor, s.borderBottomWidth, s.boxShadow].join('|'); }"
    assert pol.evaluate(style) != fleet.evaluate(style)
    shot("then")


def test_n1_register_not_in_nav_but_reachable(page, shot, app):
    page.goto("./#/")
    expect(_nav(page).get_by_role("link", name="Register external agent")).to_have_count(0)
    page.get_by_test_id("register-external-button").click()
    expect(page).to_have_url(re.compile(r"#/(register|connections/add-by-url)"))
    expect(page.get_by_role("heading", level=1, name="Register external agent")).to_be_visible()
    page.goto("./#/register")
    expect(page.get_by_role("heading", level=1, name="Register external agent")).to_be_visible()
    expect(_nav(page).get_by_role("link", name="Register external agent")).to_have_count(0)
    shot("then")


# ---------------------------------------------------------------- N-2
@pytest.mark.smoke
@pytest.mark.parametrize("route", PLACEHOLDERS)
def test_n2_placeholder_pages(page, shot, route):
    page.goto(f"./#/{route}")
    title = page.get_by_test_id("page-title")
    expect(title).to_be_visible()
    expect(title).to_have_text(re.compile(route, re.I))
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.get_by_test_id("app-topbar")).to_be_visible()
    crumb = page.get_by_role("navigation", name="Breadcrumb")
    expect(crumb).to_be_visible()
    expect(crumb).to_contain_text(re.compile(route, re.I))
    # one-line description right under the title
    desc = page.get_by_test_id("page-description")
    expect(desc).to_be_visible()
    assert len(desc.inner_text().strip()) > 15
    empty = page.get_by_test_id("empty-state")
    expect(empty).to_be_visible()
    text = empty.inner_text().strip()
    assert len(text) > 40, text  # says what will appear, not just a label
    assert not re.fullmatch(r"\W*coming soon\W*", text, re.I)
    expect(empty.get_by_role("heading")).to_be_visible()
    body = page.locator("body").inner_text()
    assert not re.search(r"unavailable|\b40[0-9]\b|\b50[0-9]\b|\{\s*\"", body, re.I), body
    expect(page.get_by_test_id(f"nav-{route}")).to_have_attribute("aria-current", "page")
    shot("then")


# ---------------------------------------------------------------- N-3
def test_n3_root_opens_fleet(page, shot, app):
    page.goto("./#/")
    expect(page.get_by_role("heading", level=1, name="Fleet")).to_be_visible()
    expect(page.get_by_test_id("nav-fleet")).to_have_attribute("aria-current", "page")
    shot("then")


def test_n3_unknown_route_not_found(page, shot):
    page.goto("./#/no-such-page")
    nf = page.get_by_test_id("not-found")
    expect(nf).to_be_visible()
    expect(nf).to_contain_text(re.compile(r"not found|doesn.t exist|no such page", re.I))
    expect(page.get_by_test_id("app-topbar")).to_be_visible()
    link = nf.get_by_role("link", name=re.compile("Fleet"))
    expect(link).to_be_visible()
    link.click()
    expect(page.get_by_role("heading", level=1, name="Fleet")).to_be_visible()
    expect(page.get_by_test_id("not-found")).to_have_count(0)
    shot("then")


# ---------------------------------------------------------------- N-4
@pytest.mark.parametrize("width", [1024, 1440])
@pytest.mark.parametrize("route", ["", "home", "findings", "policies", "connections", "audit"])
def test_n4_no_horizontal_scroll(page, shot, width, route):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"./#/{route}")
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    expect(page.get_by_test_id("nav-audit")).to_be_visible()
    settle(page)
    assert _no_hscroll(page)
    shot("then")


def test_n4_keyboard_reach_and_focus_rings(page, shot):
    page.goto("./#/findings")
    expect(page.get_by_test_id("page-title")).to_be_visible()
    for key in ORDER:
        loc = page.get_by_test_id(f"nav-{key.lower()}")
        loc.focus()
        expect(loc).to_be_focused()
        assert _ring(loc), key
    # Tab order walks the nav in order
    page.get_by_test_id("nav-home").focus()
    seen = []
    for _ in range(5):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("document.activeElement.getAttribute('data-testid')"))
    assert seen == ["nav-fleet", "nav-findings", "nav-policies", "nav-connections", "nav-audit"], seen
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"#/audit"))
    shot("then")


@pytest.mark.parametrize("width", [360, 480])
def test_n4_narrow_nav_wraps_or_scrolls_inside(page, shot, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto("./#/policies")
    expect(page.get_by_test_id("page-title")).to_be_visible()
    settle(page)
    assert _no_hscroll(page)
    nav = _nav(page)
    expect(nav.get_by_role("link")).to_have_count(7)
    # every item is either on screen or reachable by scrolling the nav itself
    page.get_by_test_id("nav-audit").scroll_into_view_if_needed()
    box = page.get_by_test_id("nav-audit").bounding_box()
    assert box and 0 <= box["x"] and box["x"] + box["width"] <= width + 1, box
    assert _no_hscroll(page)
    shot("then")


# ---------------------------------------------------------------- N-5
@pytest.mark.parametrize("route", PLACEHOLDERS + ["connections", "register"])
def test_n5_deep_links_relative(page, shot, route):
    bad = []
    page.on("response", lambda r: bad.append((r.url, r.status)) if r.status >= 400 else None)
    page.on("request", lambda r: bad.append((r.url, "absolute")) if re.match(r"https?://[^/]+/(assets|api)/", r.url) and
            not r.url.startswith(page.url.split("#")[0]) else None)
    page.goto(f"./#/{route}")
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    if route == "connections":
        expect(page.get_by_test_id("provider-gallery")).to_be_visible()
    elif route != "register":
        expect(page.get_by_test_id("page-title")).to_be_visible()
    # /register redirects to /connections/add-by-url (PO-5.1)
    assert page.url.endswith("#/connections/add-by-url" if route == "register" else f"#/{route}")
    html = page.content()
    assert not re.search(r'(?:src|href)="/(?!/)', html), "root-absolute asset URL breaks the proxy prefix"
    assert not bad, bad
    shot("then")
