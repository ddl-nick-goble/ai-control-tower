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
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(250)
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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _server import start_app  # noqa: E402


@pytest.fixture(scope="session")
def app_session(tmp_path_factory):
    """Seed a demo DB, start app.sh on a free port (own tmp dir per xdist worker); stop the app at the end."""
    app = start_app(tmp_path_factory.mktemp("act-e2e"), seed=True)
    try:
        yield {"base_url": app.url, **app.ids}
    finally:
        app.stop()


@pytest.fixture(scope="session")
def base_url(app_session):
    return app_session["base_url"]


@pytest.fixture(scope="session")
def seeded(app_session):
    """ids from the seed: recon_id, treasury_id, run_id."""
    return app_session


# ---- T-12: fixture A2A card servers ----------------------------------------------------------
import socket  # noqa: E402


def _start_card_server(signed):
    for _ in range(5):
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        cmd = [sys.executable, str(ROOT / "fixtures" / "external" / "serve.py"), "--port", str(port)]
        if signed:
            cmd.append("--signed")
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                return proc, f"http://127.0.0.1:{port}/.well-known/agent-card.json"
            except OSError:
                time.sleep(0.25)
        proc.terminate()
    raise RuntimeError("card server did not start")


@pytest.fixture(scope="session")
def card_urls():
    servers = [_start_card_server(False), _start_card_server(True)]
    try:
        yield {"unsigned": servers[0][1], "signed": servers[1][1]}
    finally:
        for proc, _ in servers:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
