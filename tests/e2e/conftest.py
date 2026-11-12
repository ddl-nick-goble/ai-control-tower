import re
import os
import pathlib

import pytest


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}}


def _run_dir():
    return pathlib.Path(os.environ.get("QA_RUN_DIR", "test-results"))


@pytest.fixture
def shot(page, request):
    """shot(step, full_page=False): a viewport PNG in $QA_RUN_DIR (default test-results/), no fixed wait.

    Regression runs take screenshots only on failure (see _failure_shot): shot() writes nothing unless QA_SHOTS=1,
    which QA/design runs set. full_page is opt-in.
    """
    on = os.environ.get("QA_SHOTS") == "1"

    def _shot(step: str, full_page: bool = False) -> str:
        path = _run_dir() / f"{request.node.originalname}-{step}.png"
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=full_page)
        return str(path)

    return _shot


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """A failed test gets a viewport screenshot of its page (taken while the page is still open) in $QA_RUN_DIR."""
    out = yield
    if call.when == "call" and out.get_result().failed:
        page = getattr(item, "funcargs", {}).get("page")
        if page is not None:
            try:
                _run_dir().mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(_run_dir() / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', item.name)}-FAILED.png"))
            except Exception:
                pass  # page already closed


# ---- apps: one per distinct environment per run (FE-3) --------------------------------------------------------
import json  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _server import AppRegistry, wait_port  # noqa: E402


@pytest.fixture(scope="session")
def app_registry(tmp_path_factory):
    """One app per distinct environment (cached by env key) for the whole run; all stopped at the end."""
    reg = AppRegistry(tmp_path_factory.mktemp("act-e2e"))
    try:
        yield reg
    finally:
        reg.stop_all()


@pytest.fixture(scope="module", autouse=True)
def _fresh_dbs(app_registry):
    """Each module starts from the seeded/initial DB of every shared app (restored from its snapshot)."""
    app_registry.reset()


@pytest.fixture(scope="session")
def apps(app_registry):
    """apps(seed=, demo=, external=, extra_env=, strip_env=) -> the cached App for that environment."""
    return app_registry.get


@pytest.fixture(scope="session")
def app_session(apps):
    """The seeded app (external cards allowed, demo off); ids from the seed are merged into the dict."""
    app = apps(seed=True)
    return {"base_url": app.url, **app.ids}


@pytest.fixture(scope="session")
def base_url(app_session):
    return app_session["base_url"]


@pytest.fixture(scope="session")
def seeded(app_session):
    """ids from the seed: recon_id, treasury_id, run_id."""
    return app_session


# ---- T-12: fixture A2A card servers ----------------------------------------------------------


def _start_card_server(signed):
    for _ in range(5):
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        cmd = [sys.executable, str(ROOT / "fixtures" / "external" / "serve.py"), "--port", str(port)]
        if signed:
            cmd.append("--signed")
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_port(port)
            return proc, f"http://127.0.0.1:{port}/.well-known/agent-card.json"
        except AssertionError:
            pass
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


# ---- smoke: ~15 tests across shell, nav, Fleet, Agent, Run, Settings load (run with -m smoke) --------------------
SMOKE = {
    "test_t19a_shell_fleet": {"test_s1_topbar_on_every_page", "test_s1_breadcrumbs", "test_s2_page_header",
                              "test_s2_summary_tiles", "test_s2_whole_row_click_opens_agent"},
    "test_t9_console": {"test_ac5_timeline_matches_api_and_renders_fast",
                        "test_agent_page_tier_card_identity_manifest_approvals"},
    "test_t19f_tables": {"test_f1_short_fleet_table_has_no_blank_viewport_under_it"},
    "test_t19b_agent_run_pages": {"test_s3_header_name_chip_tier_status_actions_right",
                                  "test_s4_header_run_id_copy_agent_link_range_count",
                                  "test_s6_existing_testids_on_agent_and_run"},
    "test_po5_connect_wizard": {"test_po52_gallery_six_cards_plus_other", "test_po53_aws_sample_walk"},
    "test_t48_settings_reachable": {"test_sr1_owner_nav_link_with_gear", "test_sr4_lists_exactly_defs_grouped"},
}


def pytest_collection_modifyitems(items):
    for item in items:
        if item.originalname in SMOKE.get(item.module.__name__, ()) and item.cls is None:
            item.add_marker(pytest.mark.smoke)
