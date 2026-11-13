"""SET-1 ST-3 e2e: with no Domino identity (owner mode off, owner someone else) Settings is the editable 4-card layout."""
import os
import pathlib
import sys

import pytest
from playwright.sync_api import expect

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from backend.settings import DEFS  # noqa: E402

_MANAGED = {d["env"] for d in DEFS.values()} | {"ACT_DEMO_MODE", "ACT_SECRET_KEY", "DOMINO_RUN_HOST_PATH", "ACT_SETTINGS_OPEN"}
_BASE = {"ACT_WORKSPACE_OWNER_MODE": "0", "DOMINO_PROJECT_OWNER": "someone.else"}


@pytest.fixture(scope="module")
def app_open(apps):
    return apps(external=False, strip_env=_MANAGED, extra_env={**_BASE, "ACT_SETTINGS_OPEN": "1"}).url


@pytest.fixture(scope="module")
def app_closed(apps):
    return apps(external=False, strip_env=_MANAGED, extra_env={**_BASE, "ACT_SETTINGS_OPEN": "0"}).url


@pytest.fixture
def browser_context_args():
    return {"viewport": {"width": 1440, "height": 900}}


QA_DIR = pathlib.Path(os.environ.get("SET1_QA_DIR", "/mnt/data/ai-control-tower/qa/SET-1"))


def _full(page, name):
    QA_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(QA_DIR / name), full_page=True)


def _open(page, base):
    page.goto(base + "#/settings")
    expect(page.get_by_role("heading", level=1, name="Settings")).to_be_visible()


def test_st3_open_shows_editable_four_cards(page, context, shot, app_open):
    context.set_extra_http_headers({})
    _open(page, app_open)
    expect(page.get_by_test_id("settings-body")).to_be_visible()
    for h in ("Demo mode", "Integrations", "Credentials", "Access"):
        expect(page.get_by_role("heading", level=2, name=h)).to_be_visible()
    expect(page.get_by_text("Sign in to view Settings")).to_have_count(0)
    expect(page.get_by_text("You can view these settings but not change them")).to_have_count(0)
    expect(page.get_by_text("Settings are read-only for you")).to_have_count(0)
    expect(page.get_by_test_id("settings-signin")).to_have_count(0)
    expect(page.get_by_test_id("settings-no-access")).to_have_count(0)
    expect(page.get_by_test_id("setting-service_accounts").get_by_role("button", name="Save")).to_be_visible()
    expect(page.get_by_text("Settings are open to anyone who can reach this app.")).to_be_visible()
    access = page.get_by_role("region", name="Access")
    expect(access).to_contain_text("Anyone who can open this app can change settings.")
    expect(access).to_contain_text("ACT_SETTINGS_OPEN=0")
    expect(access).to_contain_text("the project owner and these admins")
    _full(page, "r3-st3-open-four-cards.png")
    shot("then")


def test_st3_open_change_survives_reload(page, context, shot, app_open):
    context.set_extra_http_headers({})
    _open(page, app_open)
    el = page.get_by_test_id("setting-service_accounts")
    try:
        el.get_by_role("textbox").fill("svc-set1")
        el.get_by_role("button", name="Save").click()
        expect(el.get_by_role("status")).to_contain_text("Saved")
        page.reload()
        expect(page.get_by_test_id("setting-service_accounts").get_by_role("textbox")).to_have_value("svc-set1")
        page.get_by_test_id("setting-service_accounts").scroll_into_view_if_needed()
        _full(page, "r3-st3-after-reload.png")
        shot("then")
    finally:
        page.request.delete(app_open.rstrip("/") + "/api/settings/service_accounts")


def test_st3_flag_off_keeps_sign_in_gate(page, context, shot, app_closed):
    context.set_extra_http_headers({})
    page.goto(app_closed + "#/settings")
    expect(page.get_by_role("heading", name="Sign in to view Settings")).to_be_visible()
    expect(page.get_by_test_id("settings-body")).to_have_count(0)
    shot("then")
