"""PO-7a e2e: demo seed and walk for "Connect a provider" (plan.md Wave PE, PO-7a; ACs D7-1..D7-3).

Demo mode seeds connected sample tenants: aws + google (finished scan, New/Changed rows to review), microsoft (awaiting-import
queue, Fleet banner), databricks (needs_attention), snowflake not connected. The real DB is never seeded with sample connections.
Captures go to /mnt/data/ai-control-tower/qa/PO-7a/ only when QA_SHOTS=1.
"""
import os
import pathlib
import re
import sys

import pytest
from playwright.sync_api import expect


QA_DIR = pathlib.Path("/mnt/data/ai-control-tower/qa/PO-7a")
HEADERS = {"X-Domino-User-Id": "po7a-user", "X-Domino-User": "po7a-user", "Content-Type": "application/json"}
OWNER = {"X-Domino-User-Id": "owner-1", "X-Domino-User": "owner.user", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def demo_url(apps):
    return apps(demo=True, extra_env={"ACT_SAMPLE_TRUST_DELAY_S": "0", "ACT_SCAN_INLINE": "1"}).url


@pytest.fixture
def browser_context_args(demo_url):
    return {"base_url": demo_url, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture(autouse=True)
def _identity(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "po7a-user", "X-Domino-User": "po7a-user"})


@pytest.fixture(autouse=True)
def _fresh(context, demo_url):
    """Each test starts from the cold-start demo state."""
    context.request.post(demo_url + "api/demo/reset", headers=HEADERS)


@pytest.fixture
def qshot(page, request):
    """Like shot(), but into the PO-7a QA folder (only with QA_SHOTS=1)."""
    on = os.environ.get("QA_SHOTS") == "1"

    def _s(step):
        path = QA_DIR / f"{request.node.originalname}-{step}.png"
        if on:
            QA_DIR.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path))
        return str(path)
    return _s


def _clean(page):
    """No 'unavailable', raw-JSON look or status-code text on the current screen."""
    page.get_by_role("main").first.wait_for()
    text = page.locator("body").inner_text()
    assert not re.search(r"unavailable", text, re.I), "shows 'unavailable'"
    assert '{"' not in text, "shows raw JSON"
    assert not re.search(r"\b(500|404)\b", text), "shows a status code"


def _conns(context, url):
    r = context.request.get(url + "api/connections", headers=HEADERS)
    assert r.ok, r.text()
    body = r.json()
    return body["connections"] if isinstance(body, dict) else body


# ---- D7-1 ---------------------------------------------------------------------------------------------------------
@pytest.mark.smoke
def test_d71_cold_start_states_on_connections_and_fleet(page, shot):
    page.goto("./#/connections")
    expect(page.get_by_test_id("provider-gallery")).to_be_visible()
    for p in ("aws", "google", "microsoft"):
        expect(page.get_by_test_id(f"provider-card-{p}-status")).to_have_text(re.compile(r"Connected · \d+ agents?"))
        expect(page.get_by_test_id(f"provider-card-{p}")).to_contain_text(re.compile(r"Last scan\s*\S+"))
        expect(page.get_by_test_id(f"provider-card-{p}").get_by_test_id("sample-badge")).to_be_visible()
    for p in ("aws", "google"):
        expect(page.get_by_test_id(f"provider-card-{p}")).not_to_contain_text("Not scanned yet")
    expect(page.get_by_test_id("provider-card-databricks-status")).to_have_text(re.compile("Needs attention", re.I))
    expect(page.get_by_test_id("provider-card-databricks").get_by_test_id("sample-badge")).to_be_visible()
    expect(page.get_by_test_id("provider-card-snowflake-status")).to_have_text(re.compile("Not connected", re.I))
    expect(page.get_by_test_id("provider-card-snowflake").get_by_test_id("sample-badge")).to_have_count(0)
    expect(page.get_by_test_id("provider-card-snowflake-sample")).to_be_visible()

    # the Fleet banner for Microsoft's awaiting-import queue
    page.goto("./#/")
    banner = page.get_by_test_id("fleet-awaiting-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text(re.compile("awaiting import", re.I))

    # the AWS detail: a finished scan with New and Changed rows to review
    page.goto("./#/connections")
    page.get_by_test_id("provider-card-aws-connect").click()
    expect(page.get_by_test_id("connection-detail")).to_be_visible()
    row = page.get_by_test_id("sync-history").get_by_role("row").nth(1)
    expect(row).to_contain_text("Done")
    expect(row.get_by_role("cell").nth(3)).not_to_have_text(re.compile(r"^(0|—)$"))  # New
    expect(row.get_by_role("cell").nth(4)).not_to_have_text(re.compile(r"^(0|—)$"))  # Changed
    shot("then")


# ---- D7-2 ---------------------------------------------------------------------------------------------------------
def test_d72_walk_aws_review_override_import_fleet(page, qshot):
    page.goto("./#/connections")
    expect(page.get_by_test_id("provider-gallery")).to_be_visible()
    _clean(page)
    qshot("1-connections")
    page.get_by_test_id("provider-card-aws-connect").click()
    expect(page.get_by_test_id("connection-detail")).to_be_visible()
    _clean(page)
    qshot("2-aws-detail")

    page.get_by_test_id("sync-history").get_by_role("link").first.click()
    expect(page.get_by_test_id("scan-view")).to_be_visible()
    review = page.get_by_test_id("scan-review")
    expect(review).to_be_enabled()
    _clean(page)
    qshot("3-scan")
    review.click()

    expect(page.get_by_test_id("review-table")).to_be_visible()
    _clean(page)
    qshot("4-review")
    rows = page.get_by_test_id(re.compile(r"^review-row-\d+$"))
    expect(rows.first).to_be_visible()
    rid = rows.first.get_attribute("data-testid").rsplit("-", 1)[1]
    page.get_by_test_id(f"review-why-{rid}").click()
    expect(page.get_by_test_id(f"intake-panel-{rid}")).to_be_visible()
    _clean(page)
    qshot("5-why")
    page.get_by_test_id("intake-override-open").click()
    page.get_by_test_id("intake-override-tier-1").click()
    page.get_by_test_id("intake-override-reason").fill("Moves money on the sandbox ledger; reviewed in the demo walk")
    page.get_by_test_id("intake-save").click()
    expect(page.get_by_test_id(f"review-tier-{rid}")).to_contain_text("Set by you")
    _clean(page)
    qshot("6-override")

    imp = page.get_by_test_id("review-import")
    expect(imp).to_contain_text(re.compile(r"Import \d+ agents? as sample data"))
    imp.click()
    expect(page.get_by_test_id("import-summary")).to_be_visible(timeout=15000)
    _clean(page)
    qshot("7-summary")

    page.get_by_test_id("summary-open-fleet").click()
    pending = page.get_by_test_id("fleet-row").filter(has_text="Pending review")
    expect(pending.first).to_be_visible()
    _clean(page)
    qshot("8-fleet")


def test_d72_walk_snowflake_use_sample_data(page, qshot):
    page.goto("./#/connections")
    page.get_by_test_id("provider-card-snowflake-sample").click()
    expect(page.get_by_test_id("wizard-snowflake")).to_be_visible()
    _clean(page)
    qshot("1-step1")
    expect(page.get_by_test_id("handshake-listener-status")).to_contain_text(re.compile("detected", re.I), timeout=20000)
    expect(page).to_have_url(re.compile(r"#/connect/snowflake/[^/]+/access"), timeout=20000)
    page.get_by_test_id("permission-recheck").click()
    expect(page.get_by_test_id("permission-checklist")).to_be_visible()
    _clean(page)
    qshot("2-step2")
    page.get_by_test_id("permission-continue").click()
    expect(page.get_by_test_id("scope-tree")).to_be_visible()
    _clean(page)
    qshot("3-step3")
    page.get_by_test_id("scope-continue").click()
    expect(page.get_by_test_id("scan-view")).to_be_visible()
    expect(page.get_by_test_id("scan-review")).to_be_enabled(timeout=30000)
    expect(page.get_by_test_id("scan-status")).to_contain_text(re.compile("complete", re.I))
    _clean(page)
    qshot("4-done")


# ---- D7-3 ---------------------------------------------------------------------------------------------------------
def test_d73_seed_is_idempotent(context, demo_url):
    seeded = lambda: [c for c in _conns(context, demo_url) if c["provider"] != "snowflake"]  # the walk test adds snowflake
    first = seeded()
    assert sorted(c["provider"] for c in first) == ["aws", "databricks", "google", "microsoft"], first
    # a reseed (reset) and a repeated ensure_seeded leave the same connection count
    assert context.request.post(demo_url + "api/demo/reset", headers=HEADERS).ok
    assert len(seeded()) == 4
    import subprocess
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    code = ("import os,sys,tempfile\n"
            "d=tempfile.mkdtemp(); os.environ.update(ACT_DEMO_MODE='1', ACT_DB=d+'/act.db')\n"
            "from backend import demo, db\n"
            "from sqlalchemy import select, func\n"
            "demo.ensure_seeded()\n"
            "n=lambda: db.engine.connect().execute(select(func.count()).select_from(db.connections)).scalar()\n"
            "a=n(); demo.ensure_seeded(); b=n()\n"
            "print(a,b); sys.exit(0 if a==b==4 else 1)\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr[-2000:]


def test_d73_real_db_has_no_sample_connections_after_demo_on_off(apps, context):
    env = {"DOMINO_PROJECT_OWNER": "owner.user", "ACT_WORKSPACE_OWNER_MODE": "0"}
    url = apps(seed=True, external=False, extra_env=env).url
    assert _conns(context, url) == [] or all(not c.get("sample") for c in _conns(context, url))
    assert context.request.put(url + "api/settings/demo_mode", headers=OWNER, data={"value": True}).ok
    during = context.request.get(url + "api/connections", headers=OWNER).json()
    during = during["connections"] if isinstance(during, dict) else during
    assert sorted(c["provider"] for c in during if c["provider"] != "snowflake") == ["aws", "databricks", "google", "microsoft"], during
    assert context.request.put(url + "api/settings/demo_mode", headers=OWNER, data={"value": False}).ok
    after = context.request.get(url + "api/connections", headers=OWNER).json()
    after = after["connections"] if isinstance(after, dict) else after
    assert not [c for c in after if c.get("sample")], after
    assert len(after) == 0, after
