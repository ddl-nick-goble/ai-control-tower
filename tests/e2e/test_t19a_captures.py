"""T-19a QA viewport captures (not full-page). Output: /mnt/data/ai-control-tower/qa/T-19a/."""
import re

from playwright.sync_api import expect

from test_t19a_shell_fleet import (SHOT_DIR, app, authed, base_url, browser_context_args, demo_url,  # noqa: F401
                                   seeded)


def _snap(page, name):
    page.wait_for_timeout(300)
    page.screenshot(path=f"{SHOT_DIR}/{name}.png", full_page=False)


def _register_bottom(page, w, h):
    page.set_viewport_size({"width": w, "height": h})
    page.goto("./#/register")
    expect(page.get_by_role("heading", level=1, name="Register external agent")).to_be_visible()
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    _snap(page, f"cap-register-bottom-{w}")


def test_cap_register_bottom_1440(page, seeded):
    _register_bottom(page, 1440, 900)


def test_cap_register_bottom_1024(page, seeded):
    _register_bottom(page, 1024, 768)


def test_cap_run_timeline_scope_drift(page, seeded):
    page.goto(f"./#/runs/{seeded['run_id']}")
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()
    _snap(page, "cap-run-timeline")


def test_cap_demo_banner(page, demo_url):
    page.goto(demo_url + "#/")
    banner = page.get_by_test_id("demo-banner")
    expect(banner).to_be_visible()
    toggle = banner.get_by_role("button", name=re.compile("stand-ins"))
    if toggle.get_attribute("aria-expanded") == "true":
        _snap(page, "cap-demo-banner-expanded")
        toggle.click()
        _snap(page, "cap-demo-banner-collapsed")
    else:
        _snap(page, "cap-demo-banner-collapsed")
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        _snap(page, "cap-demo-banner-expanded")


def test_cap_fleet_sticky_header(page, seeded):
    page.goto("./#/")
    expect(page.get_by_test_id("fleet-row").first).to_be_visible()
    page.mouse.move(700, 500)
    page.mouse.wheel(0, 400)
    _snap(page, "cap-fleet-sticky-header")


def test_cap_fleet_error(page, seeded):
    page.route(re.compile(r"/api/agents(\?.*)?$"), lambda r: r.fulfill(
        status=500, content_type="application/json", body='{"detail":"database unavailable"}'))
    page.goto("./#/")
    expect(page.get_by_test_id("fleet-error")).to_be_visible()
    _snap(page, "cap-fleet-error")
