"""T-39 e2e: Settings page (ST-1..ST-7).

Own apps: A on 9893 (owner = DOMINO_PROJECT_OWNER "owner.user", generated secret key file, LLM_GATEWAY_URL set as a project
variable, demo off) and B on 9894 (ACT_SECRET_KEY set). Screenshots: /mnt/data/ai-control-tower/qa/T-39/.
Identity: owner signs in with X-Domino-User "owner.user"; a non-admin with "plain.user".
data-testid / role contract (role/label/text locators are primary):
  settings-page        page root at #/settings; h1 "Settings"; primary nav link "Settings" (owners/admins only)
  settings-no-access   plain "no access" state (text matches /no access/i) shown instead of the page for non-admins
  settings-loading     role=status "Loading settings"; settings-error role=alert + button "Try again"
  Sections (headings): "Demo mode", "Platform credentials", "Integrations". Credential blocks are role=group named
    "AWS AgentCore", "Google Agent Platform", "Microsoft Agent 365", "Databricks" (each with >=1 <input>).
  Each field is a role=group (fieldset) named by its label, containing a labelled textbox and buttons:
    "LLM provider", "LLM Gateway URL", "Break-classifier endpoint URL", "Break-classifier access key" (secret, type=password),
    "External card allowed hosts", "Trusted JWKS", "Service accounts",
    "Governance policy id (agentic bundle)", "Governance policy id (model bundle)"; button "Save".
    After Save: role=status text "Saved". Failed save: role=alert.
    Secret field once set: text "Set · changed by <user> on <date>", buttons "Replace" and "Clear", no input value, no value in DOM.
    Env-set field: text "Set by project variable", input readonly/disabled, no Save/Clear buttons.
  secret-key-warning   role=alert/status text recommending the ACT_SECRET_KEY project secret variable (only for a generated key file)
  Demo mode switch     role=switch named "Demo mode"; click opens role=dialog/alertdialog with buttons "Confirm" and "Cancel".
"""
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.request

import pytest
from playwright.sync_api import expect

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
PORT_A, PORT_B, PORT_C = "9893", "9894", "9895"
SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-39"
OWNER = {"X-Domino-User-Id": "owner-1", "X-Domino-User": "owner.user"}
PLAIN = {"X-Domino-User-Id": "plain-1", "X-Domino-User": "plain.user"}
SECRET = "s3cr3t-VALUE-9f8e7d6c"


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


# Every env var that names a setting the page manages: stripped so results do not depend on the workspace.
def _managed_env():
    """Every env name declared by backend/settings.py (derived so it cannot drift), plus legacy unprefixed names."""
    import sys
    sys.path.insert(0, str(ROOT))
    from backend import settings as _s
    names = {d["env"] for d in _s.DEFS.values()}
    return names | {"ACT_DEMO_MODE", "ACT_SECRET_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AZURE_TENANT_ID",
                    "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "DATABRICKS_HOST", "DATABRICKS_CLIENT_ID",
                    "DATABRICKS_CLIENT_SECRET"}


_MANAGED_ENV = _managed_env()


def _start(tmp, port, extra):
    if shutil.which("fuser"):
        subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(1)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ACT_DEMO", "ACT_SECRET", "LLM_GATEWAY")) and k not in _MANAGED_ENV}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PORT": port, "HOST": "0.0.0.0", "ACT_DB": str(tmp / "act.db"),
                "ACT_DB_PREWARM_COUNT": "0", "ACT_EVIDENCE_DIR": str(tmp / "ev"), "DOMINO_PROJECT_OWNER": "owner.user"})
    env.update(extra)
    log = tmp / "app.log"
    logf = open(log, "w")
    proc = subprocess.Popen(["bash", "app.sh"], cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
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
    return proc, logf, url


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
def app_a(tmp_path_factory):
    p = _start(tmp_path_factory.mktemp("t39a"), PORT_A, {"LLM_GATEWAY_URL": "http://gateway.invalid/"})
    yield p[2]
    _stop(p[0], p[1])


@pytest.fixture(scope="module")
def app_b(tmp_path_factory):
    p = _start(tmp_path_factory.mktemp("t39b"), PORT_B, {"ACT_SECRET_KEY": "e2e-test-secret-key-0123456789abcdef"})
    yield p[2]
    _stop(p[0], p[1])


@pytest.fixture(scope="module")
def app_c(tmp_path_factory):
    p = _start(tmp_path_factory.mktemp("t39c"), PORT_C, {"ACT_DEMO_MODE": "1"})
    yield p[2]
    _stop(p[0], p[1])


@pytest.fixture(scope="module")
def base_url(app_a):
    return app_a


@pytest.fixture
def browser_context_args(base_url):
    return {"base_url": base_url, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture
def owner(context):
    context.set_extra_http_headers(OWNER)


def _open(page):
    page.goto("./#/settings")
    expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()


def _field(page, label):
    return page.get_by_role("group", name=label, exact=True)


# ---------------------------------------------------------------- ST-1
def test_st1_nav_link_and_sections(page, shot, owner, base_url):
    page.goto("./#/")
    nav = page.get_by_role("navigation", name="Primary")
    nav.get_by_role("link", name="Settings").click()
    expect(page).to_have_url(re.compile(r"#/settings$"))
    expect(page.get_by_test_id("settings-page")).to_be_visible()
    expect(nav.get_by_role("link", name="Settings")).to_have_attribute("aria-current", "page")
    for h in ("Demo mode", "Platform credentials", "Integrations"):
        expect(page.get_by_role("heading", name=h, exact=True)).to_be_visible()
    for blk in ("AWS AgentCore", "Google Agent Platform", "Microsoft Agent 365", "Databricks"):
        g = _field(page, blk)
        expect(g).to_be_visible()
        assert g.locator("input").count() >= 1
    for f in ("LLM provider", "LLM Gateway URL", "Break-classifier endpoint URL", "Break-classifier access key",
              "External card allowed hosts", "Trusted JWKS", "Service accounts",
              "Governance policy id (agentic bundle)", "Governance policy id (model bundle)"):
        expect(_field(page, f)).to_be_visible()
    shot("then")


# ---------------------------------------------------------------- ST-2
def test_st2_secret_write_only(page, shot, owner):
    _open(page)
    f = _field(page, "Break-classifier access key")
    box = f.get_by_label("Break-classifier access key")
    expect(box).to_have_attribute("type", "password")
    box.fill(SECRET)
    f.get_by_role("button", name="Save").click()
    expect(f.get_by_role("status")).to_contain_text("Saved")
    expect(f).to_contain_text(re.compile(r"Set · changed by owner\.user on .*20\d\d"))
    expect(f.get_by_role("button", name="Replace")).to_be_visible()
    expect(f.get_by_role("button", name="Clear")).to_be_visible()
    for _ in range(2):
        assert SECRET not in page.content()
        assert SECRET not in page.evaluate("[...document.querySelectorAll('input,textarea')].map(e => e.value).join('|')")
        assert SECRET not in page.evaluate("document.documentElement.outerHTML")
        page.reload()
        expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()
    assert SECRET not in page.request.get("./api/settings", headers=OWNER).text()
    f = _field(page, "Break-classifier access key")
    expect(f).to_contain_text(re.compile(r"Set · changed by owner\.user on"))
    shot("then-set")
    f.get_by_role("button", name="Replace").click()
    expect(f.get_by_label("Break-classifier access key")).to_have_value("")
    f.get_by_label("Break-classifier access key").fill(SECRET + "-2")
    f.get_by_role("button", name="Save").click()
    expect(f).to_contain_text("Set · changed by")
    page.once("dialog", lambda d: d.accept())
    f.get_by_role("button", name="Clear").click()
    confirm = page.get_by_role("button", name="Confirm")
    if confirm.count():
        confirm.click()
    expect(f).not_to_contain_text("Set · changed by")
    expect(f.get_by_role("button", name="Clear")).to_have_count(0)
    shot("then")


def test_st2_generated_key_warning(page, shot, owner):
    _open(page)
    w = page.get_by_test_id("secret-key-warning")
    expect(w).to_be_visible()
    expect(w).to_contain_text("ACT_SECRET_KEY")
    expect(w).to_contain_text(re.compile("project secret|secret variable|project variable", re.I))
    shot("then")


def test_st2_no_warning_when_key_provided(page, context, shot, app_b):
    context.set_extra_http_headers(OWNER)
    page.goto(app_b + "#/settings")
    expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()
    expect(page.get_by_test_id("secret-key-warning")).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("ACT_SECRET_KEY")
    shot("then")


# ---------------------------------------------------------------- ST-3
def test_st3_env_set_is_read_only(page, shot, owner):
    _open(page)
    f = _field(page, "LLM Gateway URL")
    expect(f).to_contain_text("Set by project variable")
    box = f.get_by_label("LLM Gateway URL")
    assert box.count() == 0 or box.evaluate("e => e.readOnly || e.disabled")
    expect(f.get_by_role("button", name="Save")).to_have_count(0)
    expect(f.get_by_role("button", name="Clear")).to_have_count(0)
    other = _field(page, "External card allowed hosts")
    expect(other).not_to_contain_text("Set by project variable")
    shot("then")


# ---------------------------------------------------------------- ST-5
def test_st5_demo_switch_confirm_and_live_banner(page, shot, owner):
    _open(page)
    page.evaluate("window.__no_reload = 42")
    sw = page.get_by_role("switch", name="Demo mode")
    expect(sw).to_have_attribute("aria-checked", "false")
    expect(page.get_by_test_id("demo-banner")).to_have_count(0)
    sw.click()
    dlg = page.get_by_role("dialog").or_(page.get_by_role("alertdialog"))
    expect(dlg.first).to_be_visible()
    shot("then-confirm")
    dlg.first.get_by_role("button", name="Cancel").click()
    expect(sw).to_have_attribute("aria-checked", "false")
    expect(page.get_by_test_id("demo-banner")).to_have_count(0)
    sw.click()
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_test_id("demo-banner")).to_be_visible(timeout=15000)
    expect(sw).to_have_attribute("aria-checked", "true")
    assert page.evaluate("window.__no_reload") == 42, "page was reloaded"
    shot("then-on")
    sw.click()
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_test_id("demo-banner")).to_have_count(0, timeout=15000)
    expect(sw).to_have_attribute("aria-checked", "false")
    assert page.evaluate("window.__no_reload") == 42, "page was reloaded"
    shot("then")


def test_st5_demo_mode_variable_locks_switch(page, context, shot, app_c):
    context.set_extra_http_headers(OWNER)
    page.goto(app_c + "#/settings")
    expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()
    sw = page.get_by_role("switch", name="Demo mode")
    expect(sw).to_have_attribute("aria-checked", "true")
    expect(sw).to_be_disabled()
    expect(page.get_by_test_id("settings-page")).to_contain_text("Set by project variable")
    sw.click(force=True)
    expect(page.get_by_role("dialog").or_(page.get_by_role("alertdialog"))).to_have_count(0)
    shot("then")


# ---------------------------------------------------------------- ST-6
def test_st6_anonymous_gets_401_and_sign_in_message(page, context, shot, base_url):
    context.set_extra_http_headers({})
    assert page.request.get("./api/settings").status == 401
    page.goto("./#/settings")
    expect(page.get_by_text(re.compile(r"sign in|sign-in|log in", re.I)).first).to_be_visible()
    expect(page.get_by_role("heading", name="Platform credentials")).to_have_count(0)
    expect(page.get_by_role("button", name="Save")).to_have_count(0)
    shot("then")


def test_st6_non_admin_no_link_no_access(page, context, shot, base_url):
    context.set_extra_http_headers(PLAIN)
    page.goto("./#/")
    expect(page.get_by_role("navigation", name="Primary").get_by_role("link", name="Fleet")).to_be_visible()
    expect(page.get_by_role("navigation", name="Primary").get_by_role("link", name="Settings")).to_have_count(0)
    page.goto("./#/settings")
    na = page.get_by_test_id("settings-no-access")
    expect(na).to_be_visible()
    expect(na).to_contain_text(re.compile("no access", re.I))
    expect(page.get_by_role("heading", name="Platform credentials")).to_have_count(0)
    expect(page.get_by_role("button", name="Save")).to_have_count(0)
    assert page.request.get("./api/settings", headers=PLAIN).status in (401, 403)
    shot("then")


def test_st6_owner_sees_page(page, shot, owner):
    _open(page)
    expect(page.get_by_test_id("settings-no-access")).to_have_count(0)
    expect(page.get_by_role("heading", name="Integrations")).to_be_visible()
    shot("then")


# ---------------------------------------------------------------- ST-7
def test_st7_plain_field_saved_confirmation_and_persists(page, shot, owner):
    _open(page)
    f = _field(page, "External card allowed hosts")
    f.get_by_label("External card allowed hosts").fill("cards.example.com")
    f.get_by_role("button", name="Save").click()
    expect(f.get_by_role("status")).to_contain_text("Saved")
    page.reload()
    expect(_field(page, "External card allowed hosts").get_by_label("External card allowed hosts")).to_have_value("cards.example.com")
    shot("then")


def test_st7_loading_state(page, shot, owner):
    def slow(route):
        time.sleep(1.5)
        try:
            route.continue_()
        except Exception:
            pass  # target closed
    page.route(re.compile(r"/api/settings$"), slow)
    page.goto("./#/settings")
    expect(page.get_by_role("status").filter(has_text=re.compile("loading", re.I)).first).to_be_visible()
    shot("then")


def test_st7_error_states(page, shot, owner):
    _open(page)
    page.route(re.compile(r"/api/settings/[^/]+$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}) if r.request.method == "PUT" else r.continue_())
    f = _field(page, "Service accounts")
    f.get_by_label("Service accounts").fill("svc-one")
    f.get_by_role("button", name="Save").click()
    expect(f.get_by_role("alert")).to_be_visible()
    page.unroute(re.compile(r"/api/settings/[^/]+$"))
    page.route(re.compile(r"/api/settings$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.goto("./#/")
    page.goto("./#/settings")
    page.reload()
    expect(page.get_by_test_id("settings-error")).to_be_visible()
    expect(page.get_by_role("button", name="Try again")).to_be_visible()
    shot("then")


CONTRAST_JS = """() => {
  const lum = (r,g,b) => { const f = c => { c/=255; return c<=0.03928? c/12.92 : Math.pow((c+0.055)/1.055,2.4) }; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b) };
  const parse = s => { const m = s.match(/rgba?\\(([^)]+)\\)/); if(!m) return null; const p = m[1].split(',').map(x=>parseFloat(x)); return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1} };
  const bgOf = e => { while (e) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0.95) return c; e = e.parentElement } return {r:255,g:255,b:255} };
  const bad = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) {
    if (!n.textContent.trim()) continue; const e = n.parentElement; const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.display === 'none' || e.closest('[hidden]') || e.closest('script,style')) continue;
    const r = e.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const fg = parse(cs.color), bg = bgOf(e); if (!fg) continue;
    const l1 = lum(fg.r,fg.g,fg.b), l2 = lum(bg.r,bg.g,bg.b);
    const ratio = (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    if (ratio < ((size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5)) bad.push(e.tagName + ':' + n.textContent.trim().slice(0,30) + ':' + ratio.toFixed(2));
  }
  return bad; }"""


@pytest.mark.parametrize("w,h", [(1440, 900), (1024, 768)])
def test_st7_no_hscroll_and_contrast(page, shot, owner, w, h):
    page.set_viewport_size({"width": w, "height": h})
    _open(page)
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    bad = page.evaluate(CONTRAST_JS)
    assert not bad, bad[:10]
    shot(f"then-{w}")


def test_st7_keyboard_focus_and_no_claude(page, shot, owner):
    _open(page)
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
    assert not re.search(r"claude|anthropic", page.locator("body").inner_text(), re.I)
    shot("then")
