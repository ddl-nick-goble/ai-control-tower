"""T-19b run 2 full-page captures (1440 and 1024). Output: /mnt/data/ai-control-tower/qa/T-19b-run2/captures/."""
import json
import os
import re
import time

import pytest
from playwright.sync_api import expect

from test_t19a_shell_fleet import (PORT, DEMO_PORT, _start, _stop, app, authed, base_url,  # noqa: F401
                                   browser_context_args, demo_url, seeded)
from test_t19b_agent_run_pages import _mock_fleet

OUT = "/mnt/data/ai-control-tower/qa/T-19b-run2/captures"
WIDTHS = [(1440, 900), (1024, 768)]


@pytest.fixture(autouse=True)
def _qa_dir(monkeypatch):
    os.makedirs(OUT, exist_ok=True)
    monkeypatch.setenv("QA_RUN_DIR", "/mnt/data/ai-control-tower/qa/T-19b-run2")


def _cap(page, name, setup):
    for w, h in WIDTHS:
        page.set_viewport_size({"width": w, "height": h})
        setup()
        page.wait_for_timeout(400)
        page.screenshot(path=f"{OUT}/{name}-{w}.png", full_page=True)


def _timeline(page, rid):
    return page.request.get(f"./api/runs/{rid}/timeline").json()


def _longest_run(page, seeded):
    best = max((seeded["run_id"], seeded["run2_id"]), key=lambda r: len(_timeline(page, r)["events"]))
    return best, _timeline(page, best)


def _slow(route):
    time.sleep(3)
    try:
        route.continue_()
    except Exception:
        pass


def test_cap_agent_tier1_findings(page, seeded):
    _cap(page, "agent-tier1-findings", lambda: (page.goto(f"./#/agents/{seeded['treasury_id']}"),
         expect(page.get_by_role("heading", level=1)).to_be_visible(),
         expect(page.get_by_test_id("finding-item").first).to_be_visible()))


def test_cap_agent_empty(page, seeded):
    agents = page.request.get("./api/agents").json()
    empty = None
    for a in agents:
        f = page.request.get(f"./api/agents/{a['id']}/findings").json()
        r = page.request.get(f"./api/agents/{a['id']}/runs").json()
        if not f and not r:
            empty = a
            break
    assert empty, "no agent without findings and runs"
    print("EMPTY AGENT", empty["name"])
    _cap(page, "agent-empty", lambda: (page.goto(f"./#/agents/{empty['id']}"),
         expect(page.get_by_role("heading", level=1)).to_be_visible()))


def test_cap_agent_loading(page, seeded):
    page.route(re.compile(r"/api/agents/[^/]+(/[a-z]+)?$"), _slow)

    def s():
        page.goto("about:blank")
        page.goto(f"./#/agents/{seeded['treasury_id']}")
        expect(page.get_by_role("status").first).to_be_visible()
        page.screenshot(path=f"{OUT}/agent-loading-{page.viewport_size['width']}.png", full_page=True)
    for w, h in WIDTHS:
        page.set_viewport_size({"width": w, "height": h})
        s()
    page.unroute(re.compile(r"/api/agents/[^/]+(/[a-z]+)?$"))


def test_cap_agent_error(page, seeded):
    pat = re.compile(r"/api/agents/[^/]+$")
    page.route(pat, lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    _cap(page, "agent-error", lambda: (page.goto(f"./#/agents/{seeded['treasury_id']}"),
         expect(page.get_by_role("alert").first).to_be_visible()))


def test_cap_agent_card_error(page, seeded):
    page.route(re.compile(r"/api/agents/[^/]+/findings$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    _cap(page, "agent-card-error", lambda: (page.goto(f"./#/agents/{seeded['treasury_id']}"),
         expect(page.get_by_role("button", name="Try again").first).to_be_visible()))


def test_cap_run_default(page, seeded):
    rid, _ = _longest_run(page, seeded)
    _cap(page, "run-default", lambda: (page.goto(f"./#/runs/{rid}"),
         expect(page.get_by_test_id("timeline-event").first).to_be_visible()))


def test_cap_run_violation_filter(page, seeded):
    rid = seeded["run_id"]  # the drift run, which has violation events

    def s():
        page.goto(f"./#/runs/{rid}")
        expect(page.get_by_test_id("timeline-event").first).to_be_visible()
        page.get_by_role("group", name="Filter by event type").get_by_role("button", name="Violation").click()
    _cap(page, "run-violation-filter", s)


def test_cap_run_from_finding_span(page, seeded):
    def s():
        page.goto(f"./#/agents/{seeded['treasury_id']}")
        page.get_by_test_id("finding-timeline-link").first.click()
        expect(page.get_by_test_id("timeline-event").first).to_be_visible()
        page.wait_for_timeout(600)
    _cap(page, "run-from-finding-span", s)


def _lengthened(route):
    resp = route.fetch()
    d = resp.json()
    done = False
    for e in d["events"]:
        if e.get("kind") == "tool_call" and not done:
            e["args_redacted"] = {f"field_{k}": "value " * 6 for k in range(30)}
            done = True
    route.fulfill(status=200, json=d)


def test_cap_run_long_payload(page, seeded):
    rid, _ = _longest_run(page, seeded)
    page.route(re.compile(r"/api/runs/[^/]+/timeline$"), _lengthened)  # intercepted: one tool-call payload lengthened

    def load():
        page.goto(f"./#/runs/{rid}")
        page.reload()
        expect(page.get_by_test_id("timeline-event").first).to_be_visible()
        t = page.locator('[data-testid="timeline-payload"][data-collapsed="true"]').first
        t.scroll_into_view_if_needed()
        return t
    _cap(page, "run-long-payload-collapsed", lambda: load())

    def exp():
        t = load()
        t.locator("xpath=ancestor::*[@data-testid='timeline-event'][1]").get_by_test_id("payload-toggle").click()
    _cap(page, "run-long-payload-expanded", exp)


def test_cap_run_loading(page, seeded):
    rid, _ = _longest_run(page, seeded)
    page.route(re.compile(r"/api/runs/[^/]+/timeline$"), _slow)
    for w, h in WIDTHS:
        page.set_viewport_size({"width": w, "height": h})
        page.goto("about:blank")
        page.goto(f"./#/runs/{rid}")
        expect(page.get_by_role("status").first).to_be_visible()
        page.screenshot(path=f"{OUT}/run-loading-{w}.png", full_page=True)


def test_cap_run_error(page, seeded):
    rid, _ = _longest_run(page, seeded)
    page.route(re.compile(r"/api/runs/[^/]+/timeline$"), lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    _cap(page, "run-error", lambda: (page.goto(f"./#/runs/{rid}"),
         expect(page.get_by_role("alert").first).to_be_visible()))


def test_cap_run_empty_filter(page, seeded):
    rid, tl = _longest_run(page, seeded)
    kinds = {e["kind"] for e in tl["events"]}
    print("EVKEYS", [e for e in tl["events"] if e["kind"] == "tool_call"][:1])
    print("KINDS", kinds)
    labels = {"input": "Input", "model_call": "Model call", "tool_call": "Tool call"}
    missing = [l for k, l in labels.items() if k not in kinds]
    if not any(e.get("violation") or e.get("kind") == "violation" for e in tl["events"]):
        missing.append("Violation")
    if not missing:
        pytest.skip("every filter has events on the longest run")
    lab = missing[0]
    print("EMPTY FILTER", lab)

    def s():
        page.goto(f"./#/runs/{rid}")
        expect(page.get_by_test_id("timeline-event").first).to_be_visible()
        page.get_by_role("group", name="Filter by event type").get_by_role("button", name=lab).click()
    _cap(page, "run-empty-filter", s)


def test_cap_fleet_long_list_scrolled(page, seeded):
    _mock_fleet(page, page.request.get("./api/agents").json())

    def s():
        page.goto("./#/")
        expect(page.get_by_test_id("fleet-row")).to_have_count(60)
        page.mouse.move(600, 500)
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(400)
    for w, h in WIDTHS:
        page.set_viewport_size({"width": w, "height": h})
        s()
        page.screenshot(path=f"{OUT}/fleet-long-list-scrolled-{w}.png", full_page=False)  # viewport: shows pinned header


def test_cap_demo_banner_expanded(page, demo_url):
    def s():
        page.goto(demo_url + "#/")
        b = page.get_by_test_id("demo-banner")
        expect(b).to_be_visible()
        t = b.get_by_role("button", name=re.compile("stand-ins"))
        if t.get_attribute("aria-expanded") != "true":
            t.click()
    _cap(page, "demo-banner-expanded", s)
