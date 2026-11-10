"""T-18 e2e: redesigned "Register external agent" page (R-1..R-9).

Own app on port 9889 (the shared suite app on 9888 is never touched). Screenshots: /mnt/data/ai-control-tower/qa/T-18/.
data-testid contract (role/label locators are primary):
  register-page         page root           register-section (x3)   numbered card, contains a heading "1 Agent card" etc.
  register-summary      sticky panel with tier-badge (data-tier, text "Tier N"), summary-score, summary-host, summary-scopes-count
  card-url              input labelled "Agent card URL"; field error is role=alert-less text with data-testid card-url-error
  criterion-<name>      role=radiogroup (name = criterion name) holding five role=radio options named "1".."5"
  data-scopes           text input labelled "Data scopes"; scope-chip (text = scope), chip remove button "Remove <scope>",
                        scope-suggestion (clickable suggestions)
  register-error        alert at top of the form; buttons "Cancel" and "Register agent"
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
PORT = "9889"
SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-18"
NAMES = ["Decision impact", "Financial exposure", "Customer impact", "Autonomy", "Data sensitivity",
         "Reversibility", "Regulatory exposure"]
KEYS = ["decision_impact", "financial_exposure", "customer_impact", "autonomy", "data_sensitivity",
        "reversibility", "regulatory_exposure"]


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


# Module-scoped app on its own port; never starts the 9888 app. The conftest's session-scoped base_url is shadowed
# here, and browser_context_args is overridden at function scope (not chaining to the session one, which would
# cache this module's URL for every later module or start the 9888 app).
@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("t18")
    if shutil.which("fuser"):
        subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    time.sleep(1)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PORT": PORT, "HOST": "0.0.0.0",
           "ACT_DB": str(tmp / "act.db"), "ACT_DB_PREWARM_COUNT": "0", "ACT_EVIDENCE_DIR": str(tmp / "ev"),
           "ACT_EXTERNAL_ALLOW_HOSTS": "127.0.0.1,localhost",
           "ACT_TRUSTED_JWKS_PATH": str(ROOT / "fixtures" / "external" / "signing-key.pub.jwk.json")}
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
        url = f"http://localhost:{PORT}/"
    try:
        yield url
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=20)
        logf.close()


@pytest.fixture
def browser_context_args(base_url):
    return {"base_url": base_url, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture(autouse=True)
def authed(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "reg-user", "X-Domino-User": "reg-user"})


def _open(page):
    page.goto("./#/register")
    expect(page.get_by_role("heading", level=1, name="Register external agent")).to_be_visible()


def _group(page, key):
    return page.get_by_test_id(f"criterion-{key}")


def _set_all(page, v):
    for k in KEYS:
        _group(page, k).get_by_role("radio", name=str(v), exact=True).click()


def _no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _tier(page):
    return page.get_by_test_id("register-summary").get_by_test_id("tier-badge")


def test_r1_layout_header(page, shot):
    _open(page)
    crumb = page.get_by_role("navigation", name=re.compile("breadcrumb", re.I))
    expect(crumb).to_contain_text("Fleet")
    expect(crumb).to_contain_text("Register external agent")
    expect(crumb.get_by_role("link", name="Fleet")).to_be_visible()
    desc = page.get_by_test_id("register-page").get_by_text(re.compile(r"agent card", re.I)).first
    txt = page.get_by_test_id("register-description").inner_text()
    assert re.search(r"agent card", txt, re.I) and re.search(r"signature", txt, re.I) and re.search(r"pending review", txt, re.I), txt
    assert "\n" not in txt.strip()
    shot("then")


def test_r1_two_columns_sticky_at_1440(page, shot):
    _open(page)
    form = page.get_by_role("form", name=re.compile("register", re.I))
    summary = page.get_by_test_id("register-summary")
    expect(form).to_be_visible()
    expect(summary).to_be_visible()
    fb, sb = form.bounding_box(), summary.bounding_box()
    assert fb["width"] <= 740, fb
    assert sb["x"] >= fb["x"] + fb["width"] - 1, (fb, sb)
    assert abs(sb["y"] - fb["y"]) < 200, (fb, sb)
    for label in ("Tier", "host", "scope"):
        expect(summary).to_contain_text(re.compile(label, re.I))
    expect(summary.get_by_test_id("summary-scopes-count")).to_be_visible()
    assert _no_hscroll(page)
    page.mouse.wheel(0, 900)
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") > 100, "page too short to scroll: form not substantial"
    top = summary.bounding_box()["y"]
    assert 0 <= top < 200, f"summary not sticky, top={top}"
    shot("then")


def test_r1_single_column_at_1024(page, shot):
    page.set_viewport_size({"width": 1024, "height": 768})
    _open(page)
    form = page.get_by_role("form", name=re.compile("register", re.I))
    summary = page.get_by_test_id("register-summary")
    expect(summary).to_be_visible()
    fb, sb = form.bounding_box(), summary.bounding_box()
    assert not (sb["x"] >= fb["x"] + fb["width"] - 1 and abs(sb["y"] - fb["y"]) < 50), "still two columns"
    assert _no_hscroll(page)
    shot("then")


def test_r2_sections(page, shot):
    _open(page)
    secs = page.get_by_test_id("register-section")
    expect(secs).to_have_count(3)
    for i, title in enumerate(["Agent card", "Consequence assessment", "Data access"]):
        sec = secs.nth(i)
        expect(sec.get_by_role("heading", name=re.compile(rf"^\s*{i + 1}\W*\s*{title}", re.I))).to_be_visible()
        assert len(sec.get_by_test_id("section-helper").inner_text().strip()) > 10
    shot("then")


def test_r3_card_url_field_and_validation(page, shot):
    _open(page)
    url = page.get_by_label("Agent card URL")
    expect(url).to_be_visible()
    assert re.match(r"https?://", url.get_attribute("placeholder") or ""), url.get_attribute("placeholder")
    expect(page.get_by_test_id("card-url-help")).to_be_visible()
    err = page.get_by_test_id("card-url-error")
    expect(err).to_have_count(0)
    url.fill("not a url")
    url.blur()
    expect(err).to_be_visible()
    expect(err).to_contain_text(re.compile(r"http", re.I))
    url.fill("ftp://example.com/card")
    url.blur()
    expect(err).to_be_visible()
    url.fill("https://agent.example.com/.well-known/agent-card.json")
    url.blur()
    expect(err).to_have_count(0)
    url.fill("nope")
    url.blur()
    shot("then")


def test_r4_criteria_segmented_radiogroups(page, shot):
    _open(page)
    assert page.locator("input[type=number]").count() == 0
    groups = page.get_by_role("radiogroup")
    expect(groups).to_have_count(7)
    for key, name in zip(KEYS, NAMES):
        g = _group(page, key)
        expect(g).to_have_attribute("role", "radiogroup")
        expect(page.get_by_role("radiogroup", name=name)).to_have_count(1)
        radios = g.get_by_role("radio")
        expect(radios).to_have_count(5)
        for n in range(1, 6):
            expect(g.get_by_role("radio", name=str(n), exact=True)).to_have_count(1)
        expect(g.get_by_role("radio", name="3", exact=True)).to_be_checked()
        card = page.get_by_test_id(f"criterion-row-{key}")
        expect(card).to_contain_text(name)
        expect(card).to_contain_text("Low")
        expect(card).to_contain_text("High")
        assert card.get_by_test_id("criterion-question").inner_text().strip().endswith("?")
    shot("then")


def test_r4_keyboard_arrows(page, shot):
    _open(page)
    g = _group(page, "autonomy")
    g.get_by_role("radio", name="3", exact=True).focus()
    page.keyboard.press("ArrowRight")
    expect(g.get_by_role("radio", name="4", exact=True)).to_be_checked()
    page.keyboard.press("ArrowRight")
    expect(g.get_by_role("radio", name="5", exact=True)).to_be_checked()
    page.keyboard.press("ArrowLeft")
    expect(g.get_by_role("radio", name="4", exact=True)).to_be_checked()
    expect(g.get_by_role("radio", name="4", exact=True)).to_be_focused()
    shot("then")


def test_r5_live_tier_preview(page, shot):
    _open(page)
    with page.expect_response(lambda r: "api/tiering/preview" in r.url and r.request.method == "POST"):
        _set_all(page, 5)
    expect(_tier(page)).to_have_text(re.compile(r"Tier 1"))
    expect(_tier(page)).to_have_attribute("data-tier", "1")
    score5 = page.get_by_test_id("summary-score").inner_text()
    _set_all(page, 1)
    expect(_tier(page)).not_to_have_attribute("data-tier", "1")
    expect(_tier(page)).to_have_text(re.compile(r"Tier [234]"))
    assert page.get_by_test_id("summary-score").inner_text() != score5
    shot("then")


def test_r6_scope_chips(page, shot):
    _open(page)
    inp = page.get_by_label("Data scopes")
    chips = page.get_by_test_id("scope-chip")
    expect(page.get_by_test_id("scope-suggestion").first).to_be_visible()
    inp.fill("accounts:read")
    inp.press("Enter")
    expect(chips).to_have_count(1)
    inp.fill("payments:write,")
    expect(chips).to_have_count(2)
    inp.fill("accounts:read")
    inp.press("Enter")
    expect(chips).to_have_count(2)  # duplicate ignored
    expect(page.get_by_test_id("summary-scopes-count")).to_contain_text("2")
    inp.fill("")
    inp.press("Backspace")
    expect(chips).to_have_count(1)
    expect(chips.first).to_contain_text("accounts:read")
    page.get_by_role("button", name="Remove accounts:read").click()
    expect(chips).to_have_count(0)
    page.get_by_test_id("scope-suggestion").first.click()
    expect(chips).to_have_count(1)
    shot("then")


def test_r6_payload_scopes_list(page, shot, card_urls):
    _open(page)
    page.get_by_label("Agent card URL").fill(card_urls["signed"])
    inp = page.get_by_label("Data scopes")
    for s in ("accounts:read", "payments:write"):
        inp.fill(s)
        inp.press("Enter")
    _group(page, "autonomy").get_by_role("radio", name="4", exact=True).click()
    with page.expect_request(lambda r: r.url.endswith("api/agents/external") and r.method == "POST") as ri:
        page.get_by_role("button", name="Register agent").click()
    body = json.loads(ri.value.post_data)
    assert body["card_url"] == card_urls["signed"]
    assert body["criteria"]["data_scopes"] == ["accounts:read", "payments:write"], body
    assert body["criteria"]["autonomy"] == 4 and body["criteria"]["decision_impact"] == 3
    shot("then")


def test_r7_actions_and_disabled_until_valid(page, shot):
    _open(page)
    submit = page.get_by_role("button", name="Register agent", exact=True)
    expect(submit).to_be_disabled()
    page.get_by_label("Agent card URL").fill("garbage")
    expect(submit).to_be_disabled()
    page.get_by_label("Agent card URL").fill("http://127.0.0.1:1/card.json")
    expect(submit).to_be_enabled()
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page).not_to_have_url(re.compile(r"#/register"))
    expect(page.get_by_test_id("register-external-button")).to_be_visible()
    shot("then")


def test_r7_busy_and_no_double_submit(page, shot, card_urls):
    held = []  # POSTs held open by the test until it releases them

    def hold(route):
        held.append(route)  # neither continued nor fulfilled: the request stays pending

    page.route(re.compile(r"/api/agents/external$"), hold)
    _open(page)
    page.get_by_label("Agent card URL").fill(card_urls["signed"])
    page.get_by_role("button", name="Register agent", exact=True).click()
    busy = page.get_by_role("button", name=re.compile("Registering", re.I))
    expect(busy).to_be_visible()
    expect(busy).to_be_disabled()
    expect(busy).to_have_attribute("aria-busy", "true")
    expect(busy).to_have_text(re.compile("Registering…"))
    busy.click(force=True)
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert len(held) == 1, f"{len(held)} submissions while busy"
    shot("then")
    held[0].continue_()  # release the held request
    expect(page).to_have_url(re.compile(r"#/agents/"), timeout=20000)
    assert len(held) == 1


def test_r7_server_error_plain_language(page, shot):
    _open(page)
    page.get_by_label("Agent card URL").fill("http://169.254.169.254/latest")
    page.get_by_role("button", name="Register agent", exact=True).click()
    err = page.get_by_test_id("register-error")
    expect(err).to_be_visible(timeout=15000)
    expect(err).to_have_attribute("role", "alert")
    expect(err).to_contain_text(re.compile(r"not allowed", re.I))
    t = err.inner_text()
    assert "{" not in t and "detail" not in t.lower(), t
    # the alert sits above the first section
    assert err.bounding_box()["y"] < page.get_by_test_id("register-section").first.bounding_box()["y"]
    shot("then")


def test_r7_raw_json_error_is_humanised(page, shot):
    page.route(re.compile(r"/api/agents/external$"), lambda r: r.fulfill(
        status=422, content_type="application/json",
        body=json.dumps({"detail": [{"loc": ["body", "card_url"], "msg": "bad", "type": "value_error"}]})))
    _open(page)
    page.get_by_label("Agent card URL").fill("http://127.0.0.1:1/card.json")
    page.get_by_role("button", name="Register agent", exact=True).click()
    err = page.get_by_test_id("register-error")
    expect(err).to_be_visible()
    t = err.inner_text()
    assert "{" not in t and "detail" not in t.lower() and "[" not in t, t
    shot("then")


def test_r7_success_lands_on_agent(page, shot, card_urls):
    _open(page)
    page.get_by_label("Agent card URL").fill(card_urls["signed"])
    page.get_by_role("button", name="Register agent", exact=True).click()
    page.wait_for_function("location.hash.includes('/agents/') || document.querySelector('[data-testid=register-success]')",
                           timeout=20000)
    if "/agents/" not in page.url:
        page.get_by_test_id("register-success").get_by_role("link").first.click()
    expect(page).to_have_url(re.compile(r"#/agents/"))
    expect(page.get_by_test_id("passport")).to_be_visible(timeout=15000)
    expect(page.get_by_test_id("passport-signature")).to_have_attribute("data-signature", re.compile("."))
    shot("then")


def test_r8_focus_labels_no_claude(page, shot):
    _open(page)
    url = page.get_by_label("Agent card URL")
    url.focus()
    assert url.evaluate("e => { const s = getComputedStyle(e); return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0 || s.boxShadow !== 'none'; }")
    page.get_by_role("radio", name="3", exact=True).first.focus()
    page.keyboard.press("ArrowRight")
    focused = page.evaluate("""() => { const e = document.activeElement; const s = getComputedStyle(e);
        const p = e.closest('[role=radiogroup]') ? getComputedStyle(e.parentElement) : s;
        return [s.outlineStyle, s.outlineWidth, s.boxShadow, p.outlineStyle, p.boxShadow]; }""")
    assert any(v not in ("none", "0px") for v in focused), focused
    page.get_by_role("button", name="Cancel", exact=True).focus()
    assert page.get_by_role("button", name="Cancel", exact=True).evaluate(
        "e => { const s = getComputedStyle(e); return s.outlineStyle !== 'none' || s.boxShadow !== 'none'; }")
    unlabeled = page.evaluate("""() => [...document.querySelectorAll('input,select,textarea')].filter(e => e.type !== 'hidden')
        .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.getAttribute('aria-labelledby'))
        .map(e => e.outerHTML.slice(0, 80))""")
    assert unlabeled == [], unlabeled
    assert "claude" not in page.content().lower()
    shot("then")


def test_r9_fleet_button_opens_page(page, shot):
    page.goto("./")
    page.get_by_test_id("register-external-button").click()
    expect(page).to_have_url(re.compile(r"#/register"))
    expect(page.get_by_test_id("register-summary")).to_be_visible()
    shot("then")
