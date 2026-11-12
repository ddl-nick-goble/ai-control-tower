"""T-48 e2e: the Settings page is reachable (SR-1..SR-5, SF-4). Frontend contract (role/label/testid):
  Primary nav link "Settings" for every visitor, containing data-testid=nav-settings-icon (gear).
  settings-page root; settings-no-access (non-admin explanation: why read-only + what to do);
  signed-out visitors see a sign-in explanation instead of the form.
  settings-identity    in the page header: "Signed in as <owner> (workspace)" in workspace owner mode.
  settings-card-demo   first card; role=switch "Demo mode", current state text, what-it-changes text.
  demo-banner link "Demo settings" (admins only) -> #/settings.
  Section headings (h2): Demo, Integrations, Credentials, Access. Each setting root is data-testid=setting-<name>
    holding its label and help text; env-set values read "set by environment variable <ENV>" and are read-only.
  Access: setting-admins editable; data-testid=admins-fixed-owner shows the project owner as a fixed admin.
  Clear: secret fields' confirm button is exactly "Clear key"; non-secret fields' is exactly "Clear value".
Apps: W (workspace owner mode, no headers = owner.user) on 9896, N (owner mode off) on 9897, D (demo on, owner mode) on 9898.
"""
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

import pytest
from playwright.sync_api import expect

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from backend.settings import DEFS  # noqa: E402

SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-48-run1"
PLAIN = {"X-Domino-User-Id": "plain-1", "X-Domino-User": "plain.user"}
OWNER_NAME = "owner.user"
SECRET = "s3cr3t-VALUE-48aa"
_MANAGED = {d["env"] for d in DEFS.values()} | {"ACT_DEMO_MODE", "ACT_SECRET_KEY", "DOMINO_RUN_HOST_PATH"}


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


def _start(tmp, port, extra):
    if shutil.which("fuser"):
        subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(1)
    env = {k: v for k, v in os.environ.items() if k not in _MANAGED and not k.startswith("ACT_")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PORT": port, "HOST": "0.0.0.0", "ACT_DB": str(tmp / "act.db"),
                "ACT_DB_PREWARM_COUNT": "0", "ACT_EVIDENCE_DIR": str(tmp / "ev"), "DOMINO_PROJECT_OWNER": OWNER_NAME})
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


def _app(tmp_path_factory, name, port, extra):
    p = _start(tmp_path_factory.mktemp(name), port, extra)
    yield p[2]
    _stop(p[0], p[1])


@pytest.fixture(scope="module")
def app_w(tmp_path_factory):
    yield from _app(tmp_path_factory, "t48w", "9896", {"ACT_WORKSPACE_OWNER_MODE": "1", "LLM_GATEWAY_URL": "http://gateway.invalid/"})


@pytest.fixture(scope="module")
def app_n(tmp_path_factory):
    yield from _app(tmp_path_factory, "t48n", "9897", {"ACT_WORKSPACE_OWNER_MODE": "0"})


@pytest.fixture(scope="module")
def app_d(tmp_path_factory):
    yield from _app(tmp_path_factory, "t48d", "9898", {"ACT_WORKSPACE_OWNER_MODE": "1", "ACT_DEMO_MODE": "1"})


@pytest.fixture
def browser_context_args(app_w):
    return {"viewport": {"width": 1440, "height": 900}}


def _go(page, base, path="#/settings"):
    page.goto(base + path)


def _nav(page):
    return page.get_by_role("navigation", name="Primary")


def _open(page, base):
    _go(page, base)
    expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()


def _setting(page, name):
    return page.get_by_test_id(f"setting-{name}")


# ---------------------------------------------------------------- SR-1
def test_sr1_owner_nav_link_with_gear(page, shot, app_w):
    _go(page, app_w, "#/")
    link = _nav(page).get_by_role("link", name="Settings")
    expect(link).to_be_visible()
    expect(link.get_by_test_id("nav-settings-icon")).to_be_visible()
    link.click()
    expect(page.get_by_test_id("settings-page")).to_be_visible()
    shot("then")


def test_sr1_non_admin_link_and_explanation(page, context, shot, app_w):
    context.set_extra_http_headers(PLAIN)
    _go(page, app_w, "#/")
    link = _nav(page).get_by_role("link", name="Settings")
    expect(link).to_be_visible()
    expect(link.get_by_test_id("nav-settings-icon")).to_be_visible()
    link.click()
    na = page.get_by_test_id("settings-no-access")
    expect(na).to_be_visible()
    expect(na).to_contain_text(re.compile(r"read-only|locked|no access", re.I))
    expect(na).to_contain_text(re.compile(r"ask|contact|admin", re.I))
    expect(page.get_by_role("button", name="Save")).to_have_count(0)
    shot("then")


def test_sr1_signed_out_link_and_explanation(page, context, shot, app_n):
    context.set_extra_http_headers({})
    _go(page, app_n, "#/")
    link = _nav(page).get_by_role("link", name="Settings")
    expect(link).to_be_visible()
    expect(link.get_by_test_id("nav-settings-icon")).to_be_visible()
    link.click()
    expect(page.get_by_text(re.compile(r"sign in|sign-in|log in", re.I)).first).to_be_visible()
    expect(page.get_by_text(re.compile(r"locked|read-only|admin", re.I)).first).to_be_visible()
    expect(page.get_by_role("button", name="Save")).to_have_count(0)
    shot("then")


# ---------------------------------------------------------------- SR-2 (UI)
def test_sr2_workspace_identity_in_header(page, shot, app_w):
    _open(page, app_w)
    ident = page.get_by_test_id("settings-page").get_by_text(f"Signed in as {OWNER_NAME} (workspace)")
    expect(ident).to_be_visible()
    shot("then")


# ---------------------------------------------------------------- SR-3
def test_sr3_demo_card_first_and_confirm_off(page, shot, app_w):
    _open(page, app_w)
    card = page.get_by_test_id("settings-card-demo")
    expect(card).to_be_visible()
    sw = card.get_by_role("switch", name="Demo mode")
    expect(sw).to_have_attribute("aria-checked", "false")
    expect(card).to_contain_text(re.compile(r"\boff\b", re.I))
    expect(card).to_contain_text(re.compile(r"simulated|stand-in|never touches real data", re.I))
    first_h2 = page.get_by_test_id("settings-page").get_by_role("heading", level=2).first
    expect(first_h2).to_have_text(re.compile(r"^Demo", re.I))
    before = page.evaluate("""() => { const c = document.querySelector('[data-testid="settings-card-demo"]');
        return [...document.querySelectorAll('[data-testid^="setting-"]')].some(e => !c.contains(e)
          && (c.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_PRECEDING)) }""")
    assert not before, "a setting field precedes the demo card"
    try:
        sw.click()
        page.get_by_role("button", name="Confirm").click()
        expect(sw).to_have_attribute("aria-checked", "true", timeout=15000)
        sw.click()
        dlg = page.get_by_role("dialog").or_(page.get_by_role("alertdialog"))
        expect(dlg.first).to_be_visible()
        expect(dlg.first).to_contain_text(re.compile(r"real data|real database", re.I))
        shot("then")
        dlg.first.get_by_role("button", name="Cancel").click()
    finally:
        page.request.delete("./api/settings/demo_mode")


def test_sr3_banner_link_admins_only(page, context, shot, app_d):
    _go(page, app_d, "#/")
    banner = page.get_by_test_id("demo-banner")
    expect(banner).to_be_visible()
    link = banner.get_by_role("link", name="Demo settings")
    expect(link).to_be_visible()
    link.click()
    expect(page).to_have_url(re.compile(r"#/settings"))
    expect(page.get_by_test_id("settings-card-demo")).to_be_visible()
    shot("then")
    context.set_extra_http_headers(PLAIN)
    _go(page, app_d, "#/")
    expect(page.get_by_test_id("demo-banner")).to_be_visible()
    expect(page.get_by_test_id("demo-banner").get_by_role("link", name="Demo settings")).to_have_count(0)


# ---------------------------------------------------------------- SR-4
def test_sr4_lists_exactly_defs_grouped(page, shot, app_w):
    _open(page, app_w)
    expect(_setting(page, "admins")).to_be_visible()  # wait for the form
    ids = page.evaluate("[...document.querySelectorAll('[data-testid^=\"setting-\"]')].map(e => e.getAttribute('data-testid').slice(8))")
    assert sorted(ids) == sorted(DEFS), (set(ids) ^ set(DEFS))
    for h in ("Demo", "Integrations", "Credentials", "Access"):
        expect(page.get_by_test_id("settings-page").get_by_role("heading", level=2, name=re.compile(rf"^{h}\b"))).to_be_visible()
    section_of = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('[data-testid^="setting-"]')].map(e => {
        let n = e, h = null; while (n && !h) { let p = n.previousElementSibling; while (p && !h) { h = p.matches('h2') ? p : p.querySelector('h2'); p = p.previousElementSibling }
        n = n.parentElement; if (n && n.matches('[data-testid="settings-page"]')) break }
        return [e.getAttribute('data-testid').slice(8), h ? h.textContent : null] }))""")
    want = {"demo": "Demo", "integrations": "Integrations", "credentials": "Credentials", "access": "Access"}
    for name, d in DEFS.items():
        assert (section_of.get(name) or "").startswith(want[d["section"]]), (name, section_of.get(name))
    for name, d in DEFS.items():
        el = _setting(page, name)
        expect(el).to_contain_text(d["label"])
        if name != "llm_gateway_url":  # env-set: help is replaced by the env notice
            expect(el).to_contain_text(d["help"])
        if d["secret"]:
            assert el.locator("input[type=text]").count() == 0, f"{name}: secret shown in a text input"
            assert SECRET not in el.inner_text()
    shot("then")


def test_sr4_env_provided_is_read_only(page, shot, app_w):
    _open(page, app_w)
    el = _setting(page, "llm_gateway_url")
    expect(el).to_contain_text(re.compile(r"set by environment variable LLM_GATEWAY_URL", re.I))
    expect(el.get_by_role("button", name="Save")).to_have_count(0)
    expect(el.get_by_role("button", name=re.compile("^Clear"))).to_have_count(0)
    box = el.get_by_role("textbox")
    assert box.count() == 0 or box.first.evaluate("e => e.readOnly || e.disabled")
    shot("then")


def test_sr4_secret_masked_set_and_clear(page, shot, app_w):
    _open(page, app_w)
    el = _setting(page, "break_classifier_access_key")
    box = el.locator("input").first
    expect(box).to_have_attribute("type", "password")
    box.fill(SECRET)
    el.get_by_role("button", name="Save").click()
    expect(el).to_contain_text("Set")
    expect(el.get_by_role("button", name="Replace")).to_be_visible()
    assert SECRET not in page.evaluate("document.documentElement.outerHTML")
    el.get_by_role("button", name="Clear").click()
    page.get_by_role("button", name="Clear key", exact=True).click()
    expect(el.get_by_role("button", name="Replace")).to_have_count(0)
    shot("then")


# ---------------------------------------------------------------- SR-5
def test_sr5_admins_editable_owner_fixed(page, context, shot, app_w):
    _open(page, app_w)
    fixed = page.get_by_test_id("admins-fixed-owner")
    expect(fixed).to_be_visible()
    expect(fixed).to_contain_text(OWNER_NAME)
    expect(fixed).to_contain_text(re.compile(r"project owner|always an admin|fixed", re.I))
    el = _setting(page, "admins")
    try:
        el.get_by_role("textbox").fill("alice.admin")
        el.get_by_role("button", name="Save").click()
        expect(el.get_by_role("status")).to_contain_text("Saved")
        page.reload()
        expect(_setting(page, "admins").get_by_role("textbox")).to_have_value("alice.admin")
        expect(page.get_by_test_id("admins-fixed-owner")).to_contain_text(OWNER_NAME)
        context.set_extra_http_headers({"X-Domino-User-Id": "alice-1", "X-Domino-User": "alice.admin"})
        _go(page, app_w, "#/settings")
        expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()
        expect(page.get_by_test_id("settings-no-access")).to_have_count(0)
        shot("then")
    finally:
        context.set_extra_http_headers({})
        page.request.delete("./api/settings/admins")


# ---------------------------------------------------------------- SF-4
def test_sf4_clear_value_on_plain_field(page, shot, app_w):
    _open(page, app_w)
    el = _setting(page, "service_accounts")
    el.get_by_role("textbox").fill("svc-one")
    el.get_by_role("button", name="Save").click()
    expect(el.get_by_role("status")).to_contain_text("Saved")
    el.get_by_role("button", name="Clear").click()
    dlg = page.get_by_role("dialog")
    expect(dlg.get_by_role("button", name="Clear value", exact=True)).to_be_visible()
    expect(dlg.get_by_role("button", name="Clear key")).to_have_count(0)
    dlg.get_by_role("button", name="Clear value", exact=True).click()
    shot("then")


def test_sf4_clear_key_exact_name(page, shot, app_w):
    _open(page, app_w)
    el = _setting(page, "aws_secret_access_key")
    el.locator("input").first.fill(SECRET)
    el.get_by_role("button", name="Save").click()
    el.get_by_role("button", name="Clear").click()
    btn = page.get_by_role("dialog").get_by_role("button", name="Clear key", exact=True)
    expect(btn).to_be_visible()
    assert "(confirm)" not in page.get_by_role("dialog").inner_text()
    btn.click()
    shot("then")
