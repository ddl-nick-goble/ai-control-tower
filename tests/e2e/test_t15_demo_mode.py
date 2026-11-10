"""T-15 e2e: one-switch demo mode (D-2..D-10).

One app on port 9889 via app.sh (ACT_DEMO_MODE=1, temp ACT_DB), reset per test; the off-by-default test (last) restarts it without ACT_DEMO_MODE.
data-testid contract (suggested; role/text locators are primary):
  demo-banner  persistent banner with "Demo mode: simulated data and stand-ins" and the stand-in labels
Buttons (role=button): "Reset demo", "Approve as demo approver", "Run recon agent".
"""
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time
import urllib.request

import pytest
from playwright.sync_api import expect

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PORT = "9889"  # separate from the shared suite app on 9888 so this module never kills it
SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-15"


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


def _start(tmp_path, demo):
    if shutil.which("fuser"):
        subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    time.sleep(1)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACT_DEMO")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PORT": PORT, "HOST": "0.0.0.0",
                "ACT_DB": str(tmp_path / "act.db"), "ACT_DB_PREWARM_COUNT": "0",
                "ACT_EVIDENCE_DIR": str(tmp_path / "evidence")})
    if demo:
        env["ACT_DEMO_MODE"] = "1"
    log = tmp_path / "app.log"
    logf = open(log, "w")
    proc = subprocess.Popen(["bash", "app.sh"], cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
                            env=env, start_new_session=True)
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


_APP = {}


@pytest.fixture
def browser_context_args(browser_context_args, demo_url):
    """Point this module's pages at the demo app (the plugin's session-scoped base_url stays untouched for other modules)."""
    return {**browser_context_args, "base_url": demo_url}


@pytest.fixture(scope="module")
def demo_url(tmp_path_factory):
    """One demo-mode app for the session; the off-by-default test swaps it for a non-demo app."""
    proc, logf, url = _start(tmp_path_factory.mktemp("t15-demo"), True)
    _APP.update(proc=proc, logf=logf, url=url)
    try:
        yield url
    finally:
        _stop(_APP["proc"], _APP["logf"])


@pytest.fixture(autouse=True)
def _fresh_demo_state(request, demo_url):
    """Give each demo test the fresh D-2 state (reset is a no-op failure while the feature is missing)."""
    if "off_by_default" in request.node.name:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(demo_url + "api/demo/reset", method="POST"), timeout=60)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _identity(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "risk-officer-1", "X-Domino-User": "risk.officer"})


def _open_recon(page):
    page.goto("./#/")
    row = page.get_by_test_id("fleet-row").filter(has_text="recon-agent")
    expect(row).to_have_count(1)
    row.get_by_role("link", name="recon-agent").click()
    expect(page.get_by_role("heading", name=re.compile("recon-agent"))).to_be_visible()


def _assert_fresh_fleet(page):
    recon = page.get_by_test_id("fleet-row").filter(has_text="recon-agent")
    expect(recon).to_have_count(1, timeout=30000)
    row = page.get_by_test_id("fleet-row").filter(has_text="treasury-sweep-agent")
    expect(row).to_have_count(1)
    expect(row.get_by_test_id("tier-badge")).to_have_text(re.compile(r"Tier 1"))
    expect(row.get_by_test_id("open-findings")).to_have_text("1")
    row.get_by_role("link", name="treasury-sweep-agent").click()
    expect(page.get_by_role("heading", name=re.compile("treasury-sweep-agent"))).to_be_visible()
    item = page.get_by_test_id("finding-item")
    expect(item).to_have_count(1)
    expect(item).to_have_attribute("data-finding-type", "scope_drift")
    expect(item).to_have_attribute("data-status", "open")


def test_d9_banner_every_page(page, shot, demo_url):
    health = page.request.get("api/health").json()
    stand_ins = health["stand_ins"]
    assert health["demo_mode"] is True and len(stand_ins) >= 5
    for route in ("./#/", "./#/register"):
        page.goto(route)
        banner = page.get_by_test_id("demo-banner")
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("Demo mode: simulated data and stand-ins")
        for s in stand_ins:
            expect(banner).to_contain_text(s["label"])
        expect(page.locator("body")).not_to_contain_text(re.compile("claude", re.I))
    _open_recon(page)
    expect(page.get_by_test_id("demo-banner")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text(re.compile("claude", re.I))
    shot("then")


def test_d8_reset(page, shot, demo_url):
    _open_recon(page)
    page.get_by_role("button", name="Run recon agent").click()
    expect(page.get_by_test_id("run-item")).to_have_count(2, timeout=30000)
    page.goto("./#/")
    page.get_by_role("button", name="Reset demo").click()
    _assert_fresh_fleet(page)
    page.goto("./#/")
    _open_recon(page)
    expect(page.get_by_test_id("run-item")).to_have_count(1)
    assert page.request.post("api/demo/reset").status == 200
    shot("then")


def test_d3_governance_label(page, shot, demo_url):
    _open_recon(page)
    expect(page.get_by_text("simulated Governance (demo)").first).to_be_visible()
    shot("then")


def test_d4_classifier_label(page, shot, demo_url):
    _open_recon(page)
    expect(page.get_by_text("demo classifier (not the endpoint)").first).to_be_visible()
    dep = page.get_by_test_id("dependency-model").filter(has_text="break_classifier")
    expect(dep).to_have_count(1)
    expect(dep).to_contain_text(re.compile("champion", re.I))
    shot("then")


def test_d5_service_account(page, shot, demo_url):
    _open_recon(page)
    expect(page.get_by_text("svc-act-recon-demo").first).to_be_visible()
    expect(page.get_by_text("simulated service account (demo)").first).to_be_visible()
    expect(page.get_by_test_id("identity-flag-no-distinct")).to_have_count(0)
    shot("then")


def test_d6_demo_approver(page, shot, demo_url):
    _open_recon(page)
    page.get_by_role("button", name="Approve as demo approver").click()
    expect(page.get_by_test_id("approve-success")).to_be_visible(timeout=15000)
    expect(page.get_by_text("demo-approver-simulated").first).to_be_visible()
    expect(page.get_by_text(re.compile("approved", re.I)).first).to_be_visible()
    expect(page.get_by_text(re.compile("simulated", re.I)).first).to_be_visible()
    shot("then")


def test_d7_run_recon_agent(page, shot, demo_url):
    _open_recon(page)
    runs = page.get_by_test_id("run-item")
    expect(runs).to_have_count(1)
    page.get_by_role("button", name="Run recon agent").click()
    expect(runs).to_have_count(2, timeout=30000)
    runs.first.get_by_role("link").first.click()
    expect(page.get_by_test_id("timeline-page")).to_be_visible()
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()
    shot("then")


def test_d2_fresh_fleet(page, shot, demo_url):
    page.goto("./#/")
    _assert_fresh_fleet(page)
    shot("then")


def test_d10_off_by_default(page, shot, demo_url, tmp_path_factory):
    _stop(_APP["proc"], _APP["logf"])
    proc, logf, _url = _start(tmp_path_factory.mktemp("t15-off"), False)
    _APP.update(proc=proc, logf=logf)
    assert page.request.get("api/health").json().get("demo_mode") in (False, None)
    page.goto("./#/")
    expect(page.get_by_role("heading").first).to_be_visible()
    expect(page.get_by_test_id("demo-banner")).to_have_count(0)
    expect(page.get_by_text("Demo mode: simulated data and stand-ins")).to_have_count(0)
    for name in ("Approve as demo approver", "Run recon agent", "Reset demo"):
        expect(page.get_by_role("button", name=name)).to_have_count(0)
    assert page.request.post("api/demo/reset").status == 404
    shot("then")
    _stop(_APP["proc"], _APP["logf"])  # leave port 9888 free for later modules
