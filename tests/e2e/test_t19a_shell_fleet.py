"""T-19a e2e: app shell + Fleet page enterprise redesign (S-1, S-2, S-5) and Register carry-overs.

Own seeded app on port 9891 (and a demo-mode app on 9892 for the banner); the shared 9888 app is never touched.
Screenshots: /mnt/data/ai-control-tower/qa/T-19a/.
data-testid contract (role/label locators are primary):
  app-topbar        the top bar (landmark role=banner) holding the product name "Agent Control Tower",
                    app-subtitle (non-empty), and nav role=navigation name "Primary" with links Fleet / Register external agent
  nav "Breadcrumb"  role=navigation name "Breadcrumb" on Register, Agent and Run pages (absent on Fleet)
  fleet-tile-<key>  keys: total, tier1, open-findings, pending-review; number visible; tile is a button/link-like control
  fleet-search      not used; the search box is labelled "Search agents"
  fleet-count       result count, text "N agents" (N=1: "1 agent" also accepted)
  fleet-empty       empty state with a "Clear filters" button; fleet-skeleton  loading skeleton; fleet-error  role=alert
  Clear filters     button, visible only while a filter (search, source, tier) is active
  fleet-row         tabindex=0, Enter opens the agent; whole row clickable
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

import pytest
from playwright.sync_api import expect

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
PORT, DEMO_PORT = "9891", "9892"
SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-19a"


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


def _start(tmp, port, demo=False, seed=False):
    if shutil.which("fuser"):
        subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(1)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACT_DEMO")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PORT": port, "HOST": "0.0.0.0",
                "ACT_DB": str(tmp / "act.db"), "ACT_DB_PREWARM_COUNT": "0", "ACT_EVIDENCE_DIR": str(tmp / "ev"),
                "ACT_EXTERNAL_ALLOW_HOSTS": "127.0.0.1,localhost",
                "ACT_TRUSTED_JWKS_PATH": str(ROOT / "fixtures" / "external" / "signing-key.pub.jwk.json")})
    ids = {}
    if seed:
        out = subprocess.run([sys.executable, str(ROOT / "tests" / "e2e" / "seed.py"), str(tmp / "act.db")],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        assert out.returncode == 0, f"seed failed:\n{out.stdout}\n{out.stderr[-3000:]}"
        ids = json.loads(out.stdout.strip().splitlines()[-1])
    if demo:
        env["ACT_DEMO_MODE"] = "1"
    log = tmp / "app.log"
    logf = open(log, "w")
    proc = subprocess.Popen(["bash", "app.sh"], cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
    url, deadline = None, time.time() + 300
    while time.time() < deadline and url is None and proc.poll() is None:
        m = re.search(r"APP_URL=(\S+)", log.read_text())
        if m:
            url = m.group(1)
        else:
            time.sleep(0.5)
    assert url and url.endswith("/"), f"no APP_URL:\n{log.read_text()[-2000:]}"

    def ok(u):
        try:
            return urllib.request.urlopen(u + "api/health", timeout=10).status == 200
        except Exception:
            return False
    for _ in range(30):
        if ok(url):
            break
        time.sleep(1)
    else:
        url = f"http://localhost:{port}/"
    return proc, logf, url, ids


def _stop(proc, logf):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=20)
    except Exception:
        os.killpg(proc.pid, signal.SIGKILL)
    logf.close()


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    proc, logf, url, ids = _start(tmp_path_factory.mktemp("t19a"), PORT, seed=True)
    try:
        yield {"base_url": url, **ids}
    finally:
        _stop(proc, logf)


@pytest.fixture(scope="module")
def demo_url(tmp_path_factory):
    proc, logf, url, _ = _start(tmp_path_factory.mktemp("t19a-demo"), DEMO_PORT, demo=True)
    try:
        yield url
    finally:
        _stop(proc, logf)


@pytest.fixture(scope="module")
def base_url(app):
    return app["base_url"]


@pytest.fixture
def seeded(app):
    return app


@pytest.fixture
def browser_context_args(base_url):
    return {"base_url": base_url, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture(autouse=True)
def authed(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "t19a-user", "X-Domino-User": "t19a-user"})


# ---------------------------------------------------------------- helpers
def _fleet(page):
    page.goto("./#/")
    expect(page.get_by_role("heading", level=1, name="Fleet")).to_be_visible()
    expect(page.get_by_test_id("fleet-row").first).to_be_visible()


def _no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _left(loc):
    return loc.bounding_box()["x"]


def _has_focus_ring(loc):
    return loc.evaluate("""e => { const s = getComputedStyle(e);
        return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none'); }""")


def _topbar(page):
    return page.get_by_test_id("app-topbar")


def _primary(page):
    return page.get_by_role("navigation", name="Primary")


def _crumb(page):
    return page.get_by_role("navigation", name="Breadcrumb")


def _tile(page, key):
    return page.get_by_test_id(f"fleet-tile-{key}")


def _num(loc):
    m = re.search(r"\d+", loc.inner_text())
    assert m, loc.inner_text()
    return int(m.group())


def _count_text(page):
    return page.get_by_test_id("fleet-count")


# ---------------------------------------------------------------- S-1 shell
@pytest.mark.parametrize("route", ["./#/", "./#/register", "AGENT", "RUN"])
def test_s1_topbar_on_every_page(page, shot, seeded, route):
    route = {"AGENT": f"./#/agents/{seeded['treasury_id']}", "RUN": f"./#/runs/{seeded['run_id']}"}.get(route, route)
    page.goto(route)
    expect(page.get_by_role("heading").first).to_be_visible()
    bar = _topbar(page)
    expect(bar).to_be_visible()
    expect(bar).to_contain_text("Agent Control Tower")
    expect(page.get_by_test_id("app-subtitle")).to_be_visible()
    assert len(page.get_by_test_id("app-subtitle").inner_text().strip()) > 5
    nav = _primary(page)
    expect(nav.get_by_role("link", name="Fleet")).to_be_visible()
    expect(nav.get_by_role("link", name="Register external agent")).to_be_visible()
    shot("then")


def test_s1_current_page_indicated(page, shot):
    page.goto("./#/")
    expect(_primary(page).get_by_role("link", name="Fleet")).to_have_attribute("aria-current", "page")
    expect(_primary(page).get_by_role("link", name="Register external agent")).not_to_have_attribute("aria-current", "page")
    _primary(page).get_by_role("link", name="Register external agent").click()
    expect(page).to_have_url(re.compile(r"#/register"))
    expect(_primary(page).get_by_role("link", name="Register external agent")).to_have_attribute("aria-current", "page")
    expect(_primary(page).get_by_role("link", name="Fleet")).not_to_have_attribute("aria-current", "page")
    shot("then")


@pytest.mark.parametrize("route", ["./#/", "./#/register"])
def test_s1_topbar_aligned_with_page_content_1440(page, shot, route):
    page.goto(route)
    title = page.get_by_role("heading", level=1)
    expect(title).to_be_visible()
    brand = _topbar(page).get_by_text("Agent Control Tower").first
    assert abs(_left(brand) - _left(title)) <= 1.5, (_left(brand), _left(title))
    right_nav = _primary(page).get_by_role("link").last.bounding_box()
    body_w = page.evaluate("document.documentElement.clientWidth")
    assert right_nav["x"] + right_nav["width"] <= body_w - 8
    shot("then")


def test_s1_breadcrumbs(page, shot, seeded):
    page.goto("./#/")
    expect(page.get_by_role("heading", level=1, name="Fleet")).to_be_visible()
    expect(_crumb(page)).to_have_count(0)
    for route, last in ((f"./#/agents/{seeded['treasury_id']}", "treasury-sweep-agent"),
                        ("./#/register", "Register external agent"),
                        (f"./#/runs/{seeded['run_id']}", None)):
        page.goto(route)
        expect(page.get_by_role("heading").first).to_be_visible()
        c = _crumb(page)
        expect(c).to_be_visible()
        expect(c.get_by_role("link", name="Fleet")).to_be_visible()
        if last:
            expect(c).to_contain_text(last)
    c.get_by_role("link", name="Fleet").click()
    expect(page.get_by_role("heading", level=1, name="Fleet")).to_be_visible()
    shot("then")


def test_s1_demo_banner_above_and_aligned(page, shot, demo_url):
    page.goto(demo_url + "#/")
    banner = page.get_by_test_id("demo-banner")
    expect(banner).to_be_visible()
    bar = _topbar(page)
    expect(bar).to_be_visible()
    bb, tb = banner.bounding_box(), bar.bounding_box()
    assert bb["y"] + bb["height"] <= tb["y"] + 1, (bb, tb)
    title = page.get_by_role("heading", level=1, name="Fleet")
    banner_text = banner.locator("xpath=.//*[normalize-space(text())!=''][1]")
    assert abs(_left(banner_text) - _left(title)) <= 1.5, (_left(banner_text), _left(title))
    shot("then")


# ---------------------------------------------------------------- S-2 Fleet
def test_s2_page_header(page, shot):
    _fleet(page)
    head = page.get_by_test_id("fleet-description")
    expect(head).to_be_visible()
    txt = head.inner_text().strip()
    assert 15 < len(txt) < 200 and "\n" not in txt, txt
    btn = page.get_by_test_id("register-external-button")
    expect(btn).to_have_text(re.compile("Register external agent"))
    h = page.get_by_role("heading", level=1, name="Fleet").bounding_box()
    b = btn.bounding_box()
    assert b["x"] > h["x"] + 400, "primary action should sit at the right of the header"
    shot("then")


def test_s2_summary_tiles(page, shot):
    _fleet(page)
    rows = page.get_by_test_id("fleet-row")
    total = rows.count()
    for key in ("total", "tier1", "open-findings", "pending-review"):
        expect(_tile(page, key)).to_be_visible()
        assert re.search(r"\d", _tile(page, key).inner_text())
    assert _num(_tile(page, "total")) == total
    tier1 = rows.filter(has=page.get_by_test_id("tier-badge").filter(has_text="Tier 1")).count()
    assert _num(_tile(page, "tier1")) == tier1 >= 2
    assert _num(_tile(page, "open-findings")) >= 1
    shot("then")


def test_s2_tier1_tile_filters_table(page, shot):
    _fleet(page)
    rows = page.get_by_test_id("fleet-row")
    before = rows.count()
    _tile(page, "tier1").click()
    expect(page.get_by_test_id("tier-filter")).to_have_value("1")
    assert 2 <= rows.count() < before
    for r in rows.all():
        expect(r.get_by_test_id("tier-badge")).to_contain_text("Tier 1")
    expect(page.get_by_role("button", name="Clear filters")).to_be_visible()
    shot("then")


def test_s2_filter_bar_search_and_count(page, shot):
    _fleet(page)
    rows = page.get_by_test_id("fleet-row")
    total = rows.count()
    expect(_count_text(page)).to_have_text(re.compile(rf"^\s*{total} agents\s*$"))
    expect(page.get_by_role("button", name="Clear filters")).to_have_count(0)
    search = page.get_by_label("Search agents")
    expect(search).to_be_visible()
    search.fill("treasury")
    expect(rows).to_have_count(1)
    expect(rows.first.get_by_test_id("fleet-name")).to_contain_text("treasury-sweep-agent")
    expect(_count_text(page)).to_have_text(re.compile(r"^\s*1 agents?\s*$"))
    # owner search
    owner = rows.first.get_by_test_id("fleet-owner").inner_text().strip()
    search.fill("")
    expect(rows).to_have_count(total)
    search.fill(owner)
    assert 1 <= rows.count() <= total
    for r in rows.all():
        expect(r.get_by_test_id("fleet-owner")).to_contain_text(owner)
    search.fill("treasury")
    page.get_by_test_id("tier-filter").select_option("1")
    expect(rows).to_have_count(1)
    page.get_by_role("button", name="Clear filters").click()
    expect(rows).to_have_count(total)
    expect(search).to_have_value("")
    expect(page.get_by_test_id("tier-filter")).to_have_value("")
    expect(page.get_by_role("button", name="Clear filters")).to_have_count(0)
    page.get_by_test_id("source-filter").select_option("aws")
    expect(page.get_by_role("button", name="Clear filters")).to_be_visible()
    expect(rows).to_have_count(3)
    expect(_count_text(page)).to_have_text(re.compile(r"^\s*3 agents\s*$"))
    shot("then")


def test_s2_table_sticky_header_hover_and_cells(page, shot):
    _fleet(page)
    for col in ("Source", "Name", "Owner", "Tier", "Status", "Open findings", "Last seen"):
        expect(page.get_by_role("columnheader", name=re.compile(col, re.I))).to_be_visible()
    th = page.get_by_role("columnheader").first
    assert th.evaluate("e => getComputedStyle(e).position") == "sticky" or \
        th.evaluate("e => getComputedStyle(e.parentElement).position") == "sticky" or \
        th.evaluate("e => getComputedStyle(e.closest('thead')).position") == "sticky"
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(300)
    if page.evaluate("window.scrollY") > 50:
        assert 0 <= th.bounding_box()["y"] < 120, "header not stuck while scrolling"
    page.evaluate("window.scrollTo(0, 0)")
    rows = page.get_by_test_id("fleet-row")
    r = rows.nth(1)
    r.scroll_into_view_if_needed()
    bg0 = r.evaluate("e => getComputedStyle(e.querySelector('td')).backgroundColor")
    r.hover()
    page.wait_for_timeout(300)
    bg1 = r.evaluate("e => getComputedStyle(e.querySelector('td')).backgroundColor")
    assert bg0 != bg1, "no row hover style"
    first = rows.first
    for tid in ("fleet-source", "fleet-name", "fleet-owner", "tier-badge", "fleet-status", "open-findings",
                "fleet-last-seen"):
        expect(first.get_by_test_id(tid)).to_be_visible()
    shot("then")


def test_s2_open_findings_emphasis_only_when_positive(page, shot):
    _fleet(page)
    rows = page.get_by_test_id("fleet-row")
    styles = {}
    for r in rows.all():
        n = int(r.get_by_test_id("open-findings").inner_text().strip())
        s = r.get_by_test_id("open-findings").evaluate(
            "e => { const c = getComputedStyle(e); return [c.color, c.fontWeight, c.backgroundColor].join('|'); }")
        styles.setdefault(n > 0, set()).add(s)
    assert True in styles and False in styles
    assert not (styles[True] & styles[False]), styles
    assert len(styles[False]) == 1, "zero counts must all look the same (quiet)"
    shot("then")


def test_s2_whole_row_click_opens_agent(page, shot, seeded):
    _fleet(page)
    row = page.get_by_test_id("fleet-row").filter(has_text="treasury-sweep-agent")
    row.get_by_test_id("fleet-owner").click()
    expect(page).to_have_url(re.compile(rf"#/agents/{re.escape(seeded['treasury_id'])}"))
    page.go_back()
    _fleet(page)
    row.get_by_test_id("fleet-last-seen").click()
    expect(page).to_have_url(re.compile(r"#/agents/"))
    shot("then")


def test_s2_empty_state(page, shot):
    _fleet(page)
    page.get_by_label("Search agents").fill("zzz-no-such-agent-zzz")
    empty = page.get_by_test_id("fleet-empty")
    expect(empty).to_be_visible()
    expect(page.get_by_test_id("fleet-row")).to_have_count(0)
    assert len(empty.inner_text().strip()) > 20
    expect(_count_text(page)).to_have_text(re.compile(r"^\s*0 agents\s*$"))
    empty.get_by_role("button", name="Clear filters").click()
    expect(page.get_by_test_id("fleet-row").first).to_be_visible()
    expect(empty).to_have_count(0)
    page.get_by_label("Search agents").fill("zzz-no-such-agent-zzz")
    expect(empty).to_be_visible()
    shot("then")


def test_s2_skeleton_loading(page, shot):
    held = []
    page.route(re.compile(r"/api/agents(\?.*)?$"), lambda r: held.append(r))
    page.goto("./#/")
    sk = page.get_by_test_id("fleet-skeleton")
    expect(sk).to_be_visible()
    expect(page.get_by_role("heading", level=1, name="Fleet")).to_be_visible()
    expect(page.get_by_text(re.compile(r"^Loading agents", re.I))).to_have_count(0)
    shot("then")
    for r in held:
        r.continue_()
    expect(page.get_by_test_id("fleet-row").first).to_be_visible()
    expect(sk).to_have_count(0)


def test_s2_error_state_readable(page, shot):
    page.route(re.compile(r"/api/agents(\?.*)?$"), lambda r: r.fulfill(
        status=500, content_type="application/json", body='{"detail":"database unavailable"}'))
    page.goto("./#/")
    err = page.get_by_test_id("fleet-error")
    expect(err).to_be_visible()
    expect(err).to_have_attribute("role", "alert")
    t = err.inner_text()
    assert "{" not in t and "Traceback" not in t and len(t.strip()) > 15, t
    expect(err.get_by_role("button", name=re.compile("retry|try again", re.I))).to_be_visible()
    expect(page.get_by_role("heading", level=1, name="Fleet")).to_be_visible()
    shot("then")


# ---------------------------------------------------------------- S-5 quality
@pytest.mark.parametrize("size", [(1440, 900), (1024, 768)])
@pytest.mark.parametrize("route", ["./#/", "./#/register"])
def test_s5_no_horizontal_scroll(page, shot, size, route):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(route)
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    page.wait_for_timeout(300)
    assert _no_hscroll(page)
    shot(f"then-{size[0]}")


def test_s5_fleet_at_1024_usable(page, shot):
    page.set_viewport_size({"width": 1024, "height": 768})
    _fleet(page)
    assert _no_hscroll(page)
    for tid in ("fleet-name", "tier-badge", "open-findings"):
        expect(page.get_by_test_id("fleet-row").first.get_by_test_id(tid)).to_be_visible()
    for key in ("total", "tier1", "open-findings", "pending-review"):
        expect(_tile(page, key)).to_be_visible()
    expect(page.get_by_label("Search agents")).to_be_visible()
    shot("then")


def test_s5_visible_focus(page, shot):
    _fleet(page)
    nav_link = _primary(page).get_by_role("link", name="Register external agent")
    nav_link.focus()
    assert _has_focus_ring(nav_link)
    _primary(page).get_by_role("link", name="Fleet").focus()
    assert _has_focus_ring(_primary(page).get_by_role("link", name="Fleet"))
    tile = _tile(page, "tier1")
    tile.focus()
    assert _has_focus_ring(tile) or tile.evaluate("e => e.matches(':focus-visible')") and \
        _has_focus_ring(tile.locator("xpath=descendant-or-self::*[@tabindex or self::button or self::a][1]"))
    for loc in (page.get_by_label("Search agents"), page.get_by_test_id("source-filter"),
                page.get_by_test_id("tier-filter")):
        loc.focus()
        assert _has_focus_ring(loc)
    row = page.get_by_test_id("fleet-row").first
    row.focus()
    expect(row).to_be_focused()
    assert _has_focus_ring(row) or row.evaluate("e => getComputedStyle(e.querySelector('td')).boxShadow") != "none"
    shot("then")


def test_s5_rows_keyboard_enter_opens(page, shot, seeded):
    _fleet(page)
    row = page.get_by_test_id("fleet-row").filter(has_text="treasury-sweep-agent")
    expect(row).to_have_attribute("tabindex", "0")
    row.focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"#/agents/{re.escape(seeded['treasury_id'])}"))
    shot("then")


def test_s5_tab_reaches_rows(page, shot):
    _fleet(page)
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement && document.activeElement.closest('[data-testid=fleet-row]') !== null"):
            break
    else:
        pytest.fail("Tab never reached a fleet row")
    shot("then")


@pytest.mark.parametrize("route", ["./#/", "./#/register", "AGENT", "RUN"])
def test_s5_no_claude_anywhere(page, shot, seeded, route):
    route = {"AGENT": f"./#/agents/{seeded['treasury_id']}", "RUN": f"./#/runs/{seeded['run_id']}"}.get(route, route)
    page.goto(route)
    expect(page.get_by_role("heading").first).to_be_visible()
    expect(_topbar(page)).to_be_visible()
    html = page.content().lower()
    assert "claude" not in html
    assert "claude" not in page.locator("body").inner_text().lower()
    shot("then")


# ---------------------------------------------------------------- Register carry-overs
def _reject(page):
    page.goto("./#/register")
    expect(page.get_by_role("heading", level=1, name="Register external agent")).to_be_visible()
    url = page.get_by_label("Agent card URL")
    url.fill("http://169.254.169.254/latest")
    page.get_by_role("button", name="Register agent", exact=True).click()
    alert = page.get_by_test_id("register-error")
    expect(alert).to_be_visible(timeout=15000)
    return url, alert


def test_c1_alert_does_not_repeat_field_message(page, shot):
    url, alert = _reject(page)
    field_msg = page.get_by_test_id("card-url-error")
    expect(field_msg).to_be_visible()
    expect(field_msg).to_contain_text(re.compile(r"not allowed", re.I))
    expect(alert).not_to_contain_text(re.compile(r"not allowed", re.I))
    expect(alert).to_contain_text(re.compile(r"highlighted|fix", re.I))
    expect(url).to_have_attribute("aria-invalid", "true")
    shot("then")


def test_c2_focus_moves_after_failed_submit(page, shot):
    url, alert = _reject(page)
    ok = page.evaluate("""() => { const a = document.activeElement;
        return !!a && (a === document.querySelector('[data-testid=card-url-input]') ||
          a.matches('[data-testid=register-error]') || !!a.closest('[data-testid=register-error]') ||
          a.id === document.querySelector('label[for]') ?.htmlFor); }""")
    focused_url = url.evaluate("e => e === document.activeElement")
    focused_alert = alert.evaluate("e => e === document.activeElement || e.contains(document.activeElement)")
    assert focused_url or focused_alert, "focus stayed on the submit button"
    shot("then")


def test_c3_preview_unavailable_is_announced(page, shot):
    page.route(re.compile(r"api/tiering/preview"), lambda r: r.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom"}'))
    page.goto("./#/register")
    page.get_by_role("radio", name="5", exact=True).first.click()
    status = page.get_by_test_id("register-summary").get_by_role("status").filter(has_text="Preview unavailable")
    expect(status).to_be_visible()
    shot("then")
