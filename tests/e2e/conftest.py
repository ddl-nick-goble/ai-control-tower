import os
import pathlib

import pytest


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture
def shot(page, request):
    run_dir = pathlib.Path(os.environ.get("QA_RUN_DIR", "test-results"))
    run_dir.mkdir(parents=True, exist_ok=True)

    def _shot(step: str) -> str:
        path = run_dir / f"{request.node.originalname}-{step}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    return _shot


# ---- T-9 console: seed demo DB, start the app, expose base_url -------------------------------
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PORT = "9888"


def _free_port():
    for cmd in (["fuser", "-k", f"{PORT}/tcp"],):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True)
    time.sleep(1)


@pytest.fixture(scope="session")
def app_session(tmp_path_factory):
    """Seed a demo DB, start app.sh in the background, yield {base_url, ids...}; stop the app at the end."""
    db_path = str(tmp_path_factory.mktemp("act-e2e") / "act.db")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "e2e" / "seed.py"), db_path], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, f"seed failed:\n{out.stdout}\n{out.stderr[-3000:]}"
    ids = json.loads(out.stdout.strip().splitlines()[-1])

    _free_port()
    log_path = tmp_path_factory.mktemp("applog") / "app.log"
    logf = open(log_path, "w")
    proc = subprocess.Popen(["bash", "app.sh"], cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
                            env={**env, "ACT_DB": db_path, "ACT_DB_PREWARM_COUNT": "0", "PORT": PORT,
                                 "ACT_EVIDENCE_DIR": str(tmp_path_factory.mktemp("act-evidence"))},
                            start_new_session=True)
    try:
        url = None
        deadline = time.time() + 300  # first run may npm install + build
        while time.time() < deadline and url is None:
            if proc.poll() is not None:
                break
            m = re.search(r"APP_URL=(\S+)", log_path.read_text())
            if m:
                url = m.group(1)
            else:
                time.sleep(0.5)
        assert url, f"app did not print APP_URL:\n{log_path.read_text()[-3000:]}"
        assert url.endswith("/"), url
        time.sleep(1)
        # Prefer the printed APP_URL (Domino proxy prefix); fall back to localhost if the proxy does not serve it.
        import urllib.request
        def _ok(u):
            try:
                return urllib.request.urlopen(u + "api/health", timeout=10).status == 200
            except Exception:
                return False
        if not _ok(url):
            print(f"APP_URL {url} unreachable; falling back to http://localhost:{PORT}/")
            url = f"http://localhost:{PORT}/"
        yield {"base_url": url, **ids}
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=20)
        logf.close()


@pytest.fixture(scope="session")
def base_url(app_session):
    return app_session["base_url"]


@pytest.fixture(scope="session")
def seeded(app_session):
    """ids from the seed: recon_id, treasury_id, run_id."""
    return app_session
