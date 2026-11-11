"""T-19a e2e: Register tier preview works without sign-in and explains failures (P-2).

Own app on port 9893; the shared 9888 app is never touched. Screenshots: /mnt/data/ai-control-tower/qa/T-19a/.
No identity headers are ever set in this module.
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
PORT = "9893"
SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-19a"
MEANINGS = re.compile(r"Highest consequence|High consequence|Moderate consequence|Lowest consequence")
UNAVAILABLE = re.compile(r"unavailable", re.I)


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("t19a-p2")
    if shutil.which("fuser"):
        subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    time.sleep(1)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACT_DEMO")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PORT": PORT, "HOST": "0.0.0.0",
                "ACT_DB": str(tmp / "act.db"), "ACT_DB_PREWARM_COUNT": "0", "ACT_EVIDENCE_DIR": str(tmp / "ev"),
                "ACT_EXTERNAL_ALLOW_HOSTS": "127.0.0.1,localhost",
                "ACT_TRUSTED_JWKS_PATH": str(ROOT / "fixtures" / "external" / "signing-key.pub.jwk.json")})
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


def _mock(page, status):
    page.route(re.compile(r"api/tiering/preview"), lambda r: r.fulfill(
        status=status, content_type="application/json", body='{"detail":"nope"}'))


def test_p2_anonymous_defaults_show_tier_and_score(page, shot):
    page.goto("./#/register")
    summary = page.get_by_test_id("register-summary")
    expect(summary.get_by_test_id("tier-badge")).to_be_visible(timeout=5000)
    expect(summary.get_by_test_id("tier-badge")).to_contain_text(re.compile(r"Tier \d"))
    expect(summary.get_by_test_id("summary-score")).to_have_text("3.00 / 5", timeout=5000)
    expect(summary.get_by_role("status")).to_contain_text(MEANINGS)
    expect(summary).not_to_contain_text(UNAVAILABLE)
    expect(summary).not_to_contain_text(re.compile(r"Sign in to Domino", re.I))
    shot("then")


def test_p2_preview_401_says_sign_in(page, shot):
    _mock(page, 401)
    page.goto("./#/register")
    status = page.get_by_test_id("register-summary").get_by_role("status")
    expect(status).to_contain_text("Sign in to Domino to see the tier preview", timeout=5000)
    expect(page.get_by_test_id("register-summary")).not_to_contain_text("Preview unavailable")
    shot("then")


def test_p2_preview_500_says_try_again(page, shot):
    _mock(page, 500)
    page.goto("./#/register")
    status = page.get_by_test_id("register-summary").get_by_role("status")
    expect(status).to_contain_text("Preview unavailable. Try again in a moment.", timeout=5000)
    expect(page.get_by_test_id("register-summary")).not_to_contain_text("Sign in to Domino")
    shot("then")
