"""T-19a e2e: Register tier preview works without sign-in and explains failures (P-2).

Own app on a free port. Screenshots: /mnt/data/ai-control-tower/qa/T-19a/.
No identity headers are ever set in this module.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from _server import start_app

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
SHOT_DIR = "/mnt/data/ai-control-tower/qa/T-19a"
MEANINGS = re.compile(r"Highest consequence|High consequence|Moderate consequence|Lowest consequence")
UNAVAILABLE = re.compile(r"unavailable", re.I)


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(SHOT_DIR, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", SHOT_DIR)


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    app = start_app(tmp_path_factory.mktemp("t19a-p2"))
    try:
        yield app.url
    finally:
        app.stop()


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
