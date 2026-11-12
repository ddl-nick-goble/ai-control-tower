"""T-48f e2e: Settings follow-ups (SRF-1, SRF-3). SRF-2 is already covered by
test_t48_settings_reachable.py::test_sr4_lists_exactly_defs_grouped (page testids == backend DEFS names, exactly).

SRF-1: an env-provided setting shows the badge "Set by environment variable <VAR_NAME>" and is read-only.
SRF-3: the demo-off confirm dialog names the live count of real agents (from the app's real, non-demo DB).
"""
import pathlib
import re
import sqlite3
import sys

import pytest
from playwright.sync_api import expect

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from backend.settings import DEFS  # noqa: E402

OWNER_NAME = "owner.user"
_MANAGED = {d["env"] for d in DEFS.values()} | {"ACT_DEMO_MODE", "ACT_SECRET_KEY", "DOMINO_RUN_HOST_PATH"}
_ENV = {"DOMINO_PROJECT_OWNER": OWNER_NAME, "ACT_WORKSPACE_OWNER_MODE": "1"}


def _app(apps, seed, extra=None):
    return apps(seed=seed, external=False, strip_env=_MANAGED, extra_env={**_ENV, **(extra or {})})


@pytest.fixture(scope="module")
def app_env(apps):
    return _app(apps, False, {"LLM_GATEWAY_URL": "http://gateway.invalid/"})


@pytest.fixture(scope="module")
def app_seeded(apps):
    return _app(apps, True)


@pytest.fixture(scope="module")
def app_empty(apps):
    return _app(apps, False)


@pytest.fixture
def browser_context_args():
    return {"viewport": {"width": 1440, "height": 900}}


def _real_agent_count(app):
    con = sqlite3.connect(f"file:{app.db_path}?mode=ro", uri=True)
    try:
        return con.execute("select count(*) from agents").fetchone()[0]
    finally:
        con.close()


def _open(page, app):
    page.goto(app.url + "#/settings")
    expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()


def _turn_off_dialog(page, app):
    """Turn demo on (confirm), then click the switch again to open the turn-off confirm dialog."""
    _open(page, app)
    sw = page.get_by_test_id("settings-card-demo").get_by_role("switch", name="Demo mode")
    expect(sw).to_have_attribute("aria-checked", "false")
    sw.click()
    page.get_by_role("dialog").or_(page.get_by_role("alertdialog")).first.get_by_role("button", name="Confirm").click()
    expect(sw).to_have_attribute("aria-checked", "true", timeout=60000)
    sw.click()
    dlg = page.get_by_role("dialog").or_(page.get_by_role("alertdialog")).first
    expect(dlg).to_be_visible()
    return dlg


# ---------------------------------------------------------------- SRF-1
def test_srf1_env_badge_names_the_variable(page, shot, app_env):
    _open(page, app_env)
    el = page.get_by_test_id("setting-llm_gateway_url")
    expect(el).to_be_visible()
    var = DEFS["llm_gateway_url"]["env"]
    expect(el).to_contain_text(f"Set by environment variable {var}")
    expect(el).not_to_contain_text("project variable")
    expect(el.get_by_role("button", name="Save")).to_have_count(0)
    box = el.get_by_role("textbox")
    assert box.count() == 0 or box.first.evaluate("e => e.readOnly || e.disabled")
    shot("then")


def test_srf1_badge_only_on_env_provided_fields(page, shot, app_env):
    _open(page, app_env)
    expect(page.get_by_test_id("setting-llm_gateway_url")).to_be_visible()
    other = page.get_by_test_id("setting-external_allow_hosts")
    expect(other).not_to_contain_text("Set by environment variable")
    expect(page.get_by_test_id("settings-page")).not_to_contain_text("Set by project variable")
    shot("then")


# ---------------------------------------------------------------- SRF-3
def test_srf3_demo_off_dialog_names_real_agent_count(page, shot, app_seeded):
    n = _real_agent_count(app_seeded)
    assert n > 0, "the seeded real DB should hold agents"
    try:
        dlg = _turn_off_dialog(page, app_seeded)
        expect(dlg).to_contain_text(re.compile(rf"(?<!\d){n}(?!\d)\s+real agents?", re.I))
        shot("then")
        dlg.get_by_role("button", name="Cancel").click()
    finally:
        page.request.delete(app_seeded.url.rstrip("/") + "/api/settings/demo_mode")


def test_srf3_demo_off_dialog_when_no_real_agents(page, shot, app_empty):
    assert _real_agent_count(app_empty) == 0
    try:
        dlg = _turn_off_dialog(page, app_empty)
        expect(dlg).to_contain_text(re.compile(r"no real agents yet", re.I))
        expect(dlg).to_contain_text(re.compile(r"Fleet will be empty", re.I))
        shot("then")
        dlg.get_by_role("button", name="Cancel").click()
    finally:
        page.request.delete(app_empty.url.rstrip("/") + "/api/settings/demo_mode")
