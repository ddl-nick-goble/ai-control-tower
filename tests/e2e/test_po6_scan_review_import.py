"""PO-6 e2e: "Connect a provider" UI part 2 (scan view, review table, import summary, connection detail, single-agent
intake, Fleet entry points). Spec: build/design/provider-onboarding.md 3.5-3.9, 4.7-4.10, 8 (testids), 9;
plan.md Wave PD; provider-onboarding-plan.md PO-6.1..PO-6.5.

Sample agents are global per provider (an import makes a later scan see them as Unchanged), so each test that imports uses
its own provider: aws = demo walk, google = connection detail, microsoft = summary, databricks = Fleet link (last test); snowflake/databricks never import.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from _waits import settle

HEADERS = {"X-Domino-User-Id": "po6-user", "X-Domino-User": "po6-user", "Content-Type": "application/json"}
CRITERIA = ["decision_impact", "financial_exposure", "customer_impact", "autonomy",
            "data_sensitivity", "reversibility", "regulatory_exposure"]


@pytest.fixture(scope="module")
def base_url(apps):
    return apps(seed=True, extra_env={"ACT_SAMPLE_TRUST_DELAY_S": "0", "ACT_SCAN_INLINE": "1"}).url


@pytest.fixture
def browser_context_args(base_url):
    return {"base_url": base_url, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture(autouse=True)
def authed(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "po6-user", "X-Domino-User": "po6-user"})


# ---- helpers ------------------------------------------------------------------------------------------------------
def _verified(context, provider):
    """A sample connection that is verified (trusted, then checked), ready to scan."""
    r = context.request.post("api/connections", headers=HEADERS, data={"provider": provider, "sample": True})
    assert r.ok, r.text()
    cid = r.json()["id"]
    state = None
    for _ in range(40):
        state = context.request.get(f"api/connections/{cid}", headers=HEADERS).json()["state"]
        if state not in ("draft", "waiting"):
            break
        context.request.get("api/health")
    for _ in range(3):
        if state == "verified":
            break
        state = context.request.post(f"api/connections/{cid}/check", headers=HEADERS).json().get("state", state)
    assert state == "verified", state
    return cid


def _scan(context, cid):
    r = context.request.post(f"api/connections/{cid}/scans", headers=HEADERS)
    assert r.ok, r.text()
    sid = r.json()["scan_id"]
    for _ in range(200):
        s = context.request.get(f"api/scans/{sid}", headers=HEADERS).json()
        if s["state"] not in ("queued", "running"):
            break
    assert s["state"] == "done", s
    return sid, s


def _staged(context, sid, cls=None):
    q = f"?class={cls}" if cls else ""
    return context.request.get(f"api/scans/{sid}/staged{q}", headers=HEADERS).json()["rows"]


def _no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _rid(locator_testid_value):
    return locator_testid_value.rsplit("-", 1)[1]


def _row_ids(page):
    rows = page.get_by_test_id(re.compile(r"^review-row-\d+$"))
    return [_rid(rows.nth(i).get_attribute("data-testid")) for i in range(rows.count())]


def _review(page, cid, sid):
    page.goto(f"./#/connections/{cid}/scans/{sid}/review")
    expect(page.get_by_test_id("review-table")).to_be_visible()


# ---- demo walk (smoke) --------------------------------------------------------------------------------------------
@pytest.mark.smoke
def test_po6_demo_walk_aws_sample_scan_review_import_fleet(page, shot):
    page.goto("./#/connections")
    page.get_by_test_id("provider-card-aws-sample").click()
    expect(page.get_by_test_id("handshake-listener-status")).to_contain_text(re.compile("detected", re.I), timeout=20000)
    expect(page).to_have_url(re.compile(r"#/connect/aws/[^/]+/access"), timeout=20000)
    page.get_by_test_id("permission-recheck").click()
    page.get_by_test_id("permission-continue").click()
    expect(page.get_by_test_id("scope-tree")).to_be_visible()
    page.get_by_test_id("scope-continue").click()

    # scan view: live counters, finishes in seconds
    expect(page).to_have_url(re.compile(r"#/connections/[^/]+/scans/[^/]+$"), timeout=15000)
    expect(page.get_by_test_id("scan-view")).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    for k in ("found", "new", "changed", "unchanged", "missing", "skipped"):
        expect(page.get_by_test_id(f"scan-counter-{k}")).to_be_visible()
    review = page.get_by_test_id("scan-review")
    expect(review).to_be_enabled(timeout=30000)
    expect(page.get_by_test_id("scan-status")).to_contain_text(re.compile("complete", re.I))
    expect(page.get_by_test_id("scan-counter-new")).to_contain_text("2")
    expect(page.get_by_test_id("sample-badge").first).to_be_visible()
    review.click()

    # review: New rows with suggested tiers; open why; override one criterion with a reason
    expect(page).to_have_url(re.compile(r"/review$"))
    expect(page.get_by_test_id("review-table")).to_be_visible()
    expect(page.get_by_test_id("sample-banner")).to_be_visible()
    page.get_by_test_id("review-filter-new").click()
    new_rows = page.get_by_test_id(re.compile(r"^review-row-\d+$"))
    expect(new_rows).to_have_count(2)
    rid = _rid(new_rows.first.get_attribute("data-testid"))
    expect(page.get_by_test_id(f"review-tier-{rid}")).to_contain_text(re.compile(r"Tier [1-4]"))
    page.get_by_test_id(f"review-why-{rid}").click()
    panel = page.get_by_test_id(f"intake-panel-{rid}")
    expect(panel).to_be_visible()
    for c in CRITERIA:
        expect(panel.get_by_test_id(f"intake-criterion-{c}")).to_be_visible()
    shot("why")
    page.get_by_test_id("intake-override-open").click()
    page.get_by_test_id("intake-save").click()  # no reason: blocked with an inline error
    expect(page.get_by_text(re.compile("Enter a reason of at least 10 characters"))).to_be_visible()
    page.get_by_test_id("intake-override-tier-1").click()
    page.get_by_test_id("intake-override-reason").fill("Moves money on the sandbox ledger; reviewed by walk test")
    page.get_by_test_id("intake-save").click()
    expect(page.get_by_test_id(f"review-tier-{rid}")).to_contain_text(re.compile(r"Set by you"))

    # select all New, import (button states the count and says sample data)
    sel = page.get_by_test_id("review-select-all")
    if not sel.is_checked():
        sel.check()
    expect(page.get_by_test_id("review-selected-count")).to_contain_text("2")
    imp = page.get_by_test_id("review-import")
    expect(imp).to_contain_text(re.compile(r"Import 2 agents as sample data"))
    imp.click()

    # summary
    expect(page).to_have_url(re.compile(r"/summary$"), timeout=15000)
    expect(page.get_by_test_id("import-summary")).to_be_visible()
    expect(page.get_by_test_id("import-summary-count-new")).to_contain_text("2")
    expect(page.get_by_test_id("import-summary-count-no-owner")).to_be_visible()
    expect(page.get_by_test_id("sync-schedule-daily")).to_be_checked()
    shot("then")
    page.get_by_test_id("summary-open-fleet").click()
    expect(page).to_have_url(re.compile(r"#/(\?|fleet|agents)?"))
    rows = page.get_by_test_id("fleet-row")
    expect(rows.filter(has_text="Pending review").first).to_be_visible()
    expect(rows.filter(has_text="Pending review").first.get_by_test_id("sample-badge")).to_be_visible()
    shot("fleet")


# ---- PO-6.1 scan view ---------------------------------------------------------------------------------------------
def test_po61_scan_view_counters_job_link_aria_live_and_reload(page, context, shot):
    cid = _verified(context, "snowflake")
    started = context.request.post(f"api/connections/{cid}/scans", headers=HEADERS).json()
    sid = started["scan_id"]
    page.goto(f"./#/connections/{cid}/scans/{sid}")
    expect(page.get_by_test_id("scan-view")).to_be_visible()
    expect(page.get_by_test_id("scan-status")).to_have_attribute("aria-live", "polite")
    expect(page.get_by_test_id("scan-progress")).to_have_attribute("role", "progressbar")
    expect(page.get_by_test_id("scan-review")).to_be_enabled(timeout=30000)
    counts = context.request.get(f"api/scans/{sid}", headers=HEADERS).json()["counts"]
    for k in ("found", "new", "changed", "unchanged", "missing"):
        expect(page.get_by_test_id(f"scan-counter-{k}")).to_contain_text(str(counts[k]))
    expect(page.get_by_test_id("scan-feed")).to_be_visible()
    expect(page.get_by_test_id(re.compile(r"^scan-agent-row-")).first).to_be_visible()
    expect(page.get_by_test_id(re.compile(r"^scan-scope-row-")).first).to_be_visible()
    expect(page.get_by_test_id("scan-job")).to_be_visible()
    expect(page.get_by_test_id("scan-job-link")).to_be_visible()
    expect(page.get_by_test_id("sample-badge").first).to_be_visible()
    expect(page.get_by_test_id("scan-leave")).to_be_visible()
    shot("then")
    # survives a reload: progress and counters restored
    page.reload()
    expect(page.get_by_test_id("scan-view")).to_be_visible()
    expect(page.get_by_test_id("scan-review")).to_be_enabled(timeout=30000)
    for k in ("found", "new", "unchanged"):
        expect(page.get_by_test_id(f"scan-counter-{k}")).to_contain_text(str(counts[k]))
    expect(page.get_by_test_id("scan-status")).to_contain_text(re.compile("complete", re.I))
    shot("reloaded")


def test_po61_scan_view_midflight_reload_restores_progress(page, context, shot):
    cid = _verified(context, "databricks")
    sid = context.request.post(f"api/connections/{cid}/scans", headers=HEADERS).json()["scan_id"]
    # the scan the page loads is still running (server answers running), then the real state arrives after a reload
    page.route(re.compile(r".*/api/scans/[^/?]+$"), lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({
            "state": "running", "job_url": None, "updated_at": "2026-11-12T00:00:00Z",
            "progress": [{"scope": "s1", "found": 1, "errors": 0, "state": "done"},
                         {"scope": "s2", "found": 0, "errors": 0, "state": "running"}],
            "counts": {"found": 1, "new": 1, "changed": 0, "unchanged": 0, "missing": 0}})))
    page.goto(f"./#/connections/{cid}/scans/{sid}")
    expect(page.get_by_test_id("scan-counter-found")).to_contain_text("1")
    expect(page.get_by_test_id("scan-review")).to_be_disabled()
    page.reload()
    expect(page.get_by_test_id("scan-counter-found")).to_contain_text("1")
    expect(page.get_by_test_id("scan-review")).to_be_disabled()
    expect(page.get_by_test_id("scan-progress")).to_have_attribute("aria-valuenow", "1")
    shot("then")


def test_po61_scan_error_state(page, context, shot):
    cid = _verified(context, "snowflake")
    page.route(re.compile(r".*/api/scans/[^/?]+$"), lambda route: route.fulfill(status=500, body="boom"))
    page.goto(f"./#/connections/{cid}/scans/does-not-matter")
    expect(page.get_by_test_id("scan-error")).to_be_visible()
    expect(page.get_by_test_id("scan-retry")).to_be_visible()
    shot("then")


def test_po61_job_failed_state_keeps_found_agents(page, context, shot):
    cid = _verified(context, "databricks")
    sid, _ = _scan(context, cid)
    page.route(re.compile(r".*/api/scans/[^/?]+$"), lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({
            "state": "failed", "job_url": "https://example.test/jobs/4821", "updated_at": "2026-11-12T00:00:00Z",
            "progress": [{"scope": "s1", "found": 2, "errors": 1, "state": "failed"}],
            "counts": {"found": 2, "new": 2, "changed": 0, "unchanged": 0, "missing": 0}})))
    page.goto(f"./#/connections/{cid}/scans/{sid}")
    expect(page.get_by_test_id("scan-error")).to_contain_text(re.compile("stopped before it finished", re.I))
    expect(page.get_by_test_id("scan-retry")).to_be_visible()
    shot("then")


# ---- PO-6.2 review table ------------------------------------------------------------------------------------------
def test_po62_tabs_counts_default_selection_and_flags(page, context, shot):
    cid = _verified(context, "snowflake")
    sid, s = _scan(context, cid)
    n = s["counts"]
    _review(page, cid, sid)
    for k in ("new", "changed", "unchanged", "missing"):
        expect(page.get_by_test_id(f"review-filter-{k}")).to_contain_text(str(n[k]))
    expect(page.get_by_test_id("review-filter-all")).to_be_visible()
    expect(page.get_by_test_id("review-filter-attention")).to_be_visible()
    expect(page.get_by_test_id("sample-banner")).to_be_visible()
    page.get_by_test_id("review-filter-new").click()
    expect(page.get_by_test_id("review-filter-new")).to_have_attribute("aria-pressed", "true")
    rids = _row_ids(page)
    assert len(rids) == n["new"] > 0
    for rid in rids:
        expect(page.get_by_test_id(f"review-select-{rid}")).to_be_checked()  # New rows are selected by default
        expect(page.get_by_test_id(f"review-row-{rid}").get_by_test_id("sample-badge")).to_be_visible()
        expect(page.get_by_test_id(f"review-tier-{rid}")).to_contain_text(re.compile(r"Tier [1-4]"))
        expect(page.get_by_test_id(f"review-owner-{rid}")).to_be_visible()
    # ownerless rows carry the No owner flag (sample agents are discovered without an owner)
    ownerless = [r for r in _staged(context, sid, "new") if not r["owner"]]
    for r in ownerless:
        expect(page.get_by_test_id(f"review-flag-{r['id']}").first).to_contain_text(re.compile("No owner", re.I))
    # unchanged rows have no checkbox
    page.get_by_test_id("review-filter-unchanged").click()
    expect(page.get_by_test_id(re.compile(r"^review-select-\d+$"))).to_have_count(0)
    shot("then")


def test_po62_changed_rows_show_diff_and_risk_flags_missing_has_no_checkbox(page, context, shot):
    cid = _verified(context, "snowflake")
    sid, _ = _scan(context, cid)
    crit = {c: {"score": 5 if c == "financial_exposure" else 3, "reason": f"{c} evidence line"} for c in CRITERIA}
    base = {"sample": True, "overrides": {}, "summary": "x", "owner": None, "suggestion": {"tier": 1, "suggestions": crit}}
    rows = [
        {**base, "id": 9001, "class": "changed", "name": "fx-trader", "native_id": "n1",
         "flags": ["Tier 1", "No distinct identity", "Write scopes", "No owner", "Redacted secrets found"],
         "diff": [{"field": "tools", "op": "add", "after": "fx.execute_trade"},
                  {"field": "guardrail", "op": "remove", "before": "pii-filter"}]},
        {**base, "id": 9002, "class": "missing", "name": "gone-agent", "native_id": "n2", "flags": [], "diff": None},
    ]

    def staged(route):
        url = route.request.url
        m = re.search(r"class=(\w+)", url)
        body = [r for r in rows if not m or r["class"] == m.group(1)]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"rows": body}))
    page.route(re.compile(r".*/api/scans/[^/]+/staged.*"), staged)
    _review(page, cid, sid)
    page.get_by_test_id("review-filter-changed").click()
    expect(page.get_by_test_id("review-row-9001")).to_be_visible()
    expect(page.get_by_test_id("review-change-9001")).to_contain_text(re.compile("Changed", re.I))
    flags = page.get_by_test_id("review-flag-9001")
    expect(flags.first).to_be_visible()
    page.get_by_test_id("review-why-9001").click()
    diff = page.get_by_test_id("review-diff-9001")
    expect(diff).to_contain_text("fx.execute_trade")
    expect(diff).to_contain_text("pii-filter")
    shot("diff")
    page.get_by_test_id("review-filter-missing").click()
    expect(page.get_by_test_id("review-row-9002")).to_be_visible()
    expect(page.get_by_test_id("review-select-9002")).to_have_count(0)
    expect(page.get_by_test_id("review-row-9002")).to_contain_text(re.compile("finding", re.I))
    shot("then")


def test_po62_override_requires_reason_and_shows_set_by_you(page, context, shot):
    cid = _verified(context, "databricks")
    sid, _ = _scan(context, cid)
    _review(page, cid, sid)
    page.get_by_test_id("review-filter-new").click()
    rid = _row_ids(page)[0]
    page.get_by_test_id(f"review-why-{rid}").click()
    expect(page.get_by_test_id(f"review-why-{rid}")).to_have_attribute("aria-expanded", "true")
    for c in CRITERIA:
        expect(page.get_by_test_id(f"intake-criterion-{c}")).to_be_visible()
    page.get_by_test_id("intake-override-open").click()
    expect(page.get_by_test_id("intake-override")).to_be_visible()
    page.get_by_test_id("intake-override-tier-4").click()
    page.get_by_test_id("intake-override-reason").fill("short")
    page.get_by_test_id("intake-save").click()
    expect(page.get_by_text("Enter a reason of at least 10 characters")).to_be_visible()
    expect(page.get_by_test_id(f"review-tier-{rid}")).not_to_contain_text("Set by you")
    page.get_by_test_id("intake-override-reason").fill("Internal sandbox only, no customer data")
    page.get_by_test_id("intake-save").click()
    expect(page.get_by_test_id(f"review-tier-{rid}")).to_contain_text("Set by you")
    saved = [r for r in _staged(context, sid, "new") if str(r["id"]) == rid][0]
    assert saved["overrides"], saved
    shot("then")


def test_po62_bulk_select_bulk_owner_and_import_button_states_count(page, context, shot):
    cid = _verified(context, "databricks")
    sid, s = _scan(context, cid)
    _review(page, cid, sid)
    page.get_by_test_id("review-filter-new").click()
    total = s["counts"]["new"]
    assert total >= 2
    expect(page.get_by_test_id("review-bulk-bar")).to_be_visible()
    imp = page.get_by_test_id("review-import")
    expect(imp).to_contain_text(re.compile(rf"Import {total} agents"))
    expect(page.get_by_test_id("review-selected-count")).to_contain_text(str(total))
    # clear selection: disabled with a reason; select one: states 1
    page.get_by_test_id("review-select-all").uncheck()
    expect(imp).to_be_disabled()
    expect(page.get_by_text("Select at least one agent to import")).to_be_visible()
    rids = _row_ids(page)
    page.get_by_test_id(f"review-select-{rids[0]}").check()
    expect(imp).to_contain_text(re.compile(r"Import 1 agent"))
    page.get_by_test_id("review-select-all").check()
    # bulk owner
    bulk = page.get_by_test_id("review-bulk-owner")
    opts = bulk.locator("option")
    assert opts.count() >= 2
    label = [o for o in opts.all_inner_texts() if o.strip() and not re.search(r"assign|no owner", o, re.I)][0]
    bulk.select_option(label=label)
    for rid in rids:
        expect(page.get_by_test_id(f"review-owner-{rid}")).to_contain_text(label.strip())
    shot("then")


def test_po62_search_and_empty_filter(page, context, shot):
    cid = _verified(context, "snowflake")
    sid, _ = _scan(context, cid)
    _review(page, cid, sid)
    page.get_by_test_id("review-search").fill("zzz-no-such-agent-zzz")
    expect(page.get_by_test_id("review-empty")).to_contain_text(re.compile("No agents match", re.I))
    page.get_by_test_id("review-search").fill("")
    expect(page.get_by_test_id("review-empty")).to_have_count(0)
    shot("then")


@pytest.mark.parametrize("width", [1024, 1440])
def test_po62_review_table_no_horizontal_scroll(page, context, width, shot):
    cid = _verified(context, "snowflake")
    sid, _ = _scan(context, cid)
    page.set_viewport_size({"width": width, "height": 800})
    _review(page, cid, sid)
    expect(page.get_by_test_id(re.compile(r"^review-row-\d+$")).first).to_be_visible()
    settle(page)
    assert _no_hscroll(page), f"horizontal scroll at {width}"
    rid = _row_ids(page)[0]
    page.get_by_test_id(f"review-why-{rid}").click()
    settle(page)
    assert _no_hscroll(page), f"horizontal scroll with why open at {width}"
    shot("then")


def test_po62_staged_error_state(page, context, shot):
    cid = _verified(context, "snowflake")
    sid, _ = _scan(context, cid)
    page.route(re.compile(r".*/api/scans/[^/]+/staged.*"), lambda route: route.fulfill(status=500, body="boom"))
    page.goto(f"./#/connections/{cid}/scans/{sid}/review")
    expect(page.get_by_role("alert").first).to_be_visible()
    expect(page.get_by_test_id("review-import")).to_have_count(0)
    shot("then")


def test_po62_import_error_shows_alert_and_keeps_selection(page, context, shot):
    cid = _verified(context, "snowflake")
    sid, _ = _scan(context, cid)
    _review(page, cid, sid)
    page.route(re.compile(r".*/api/scans/[^/]+/import$"), lambda route: route.fulfill(status=500, body="db timeout"))
    page.get_by_test_id("review-import").click()
    expect(page.get_by_test_id("review-import-error")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/review$"))
    expect(page.get_by_test_id("review-bulk-bar")).to_be_visible()
    shot("then")


# ---- PO-6.3 import summary + connection detail --------------------------------------------------------------------
def test_po63_connection_detail_scan_now_disconnect_keeps_agents(page, context, shot):
    cid = _verified(context, "google")
    sid, _ = _scan(context, cid)
    new = _staged(context, sid, "new")
    out = context.request.post(f"api/scans/{sid}/import", headers=HEADERS, data={"ids": [r["id"] for r in new]}).json()
    assert out["imported"] == len(new) > 0
    page.goto(f"./#/connections/{cid}")
    expect(page.get_by_test_id("connection-detail")).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.get_by_test_id("connection-status")).to_be_visible()
    expect(page.get_by_test_id("sample-badge").first).to_be_visible()
    expect(page.get_by_test_id("connection-status")).not_to_have_text(re.compile(r"^\s*Connected\s*$", re.I))
    expect(page.get_by_test_id("connection-check-access")).to_be_visible()
    expect(page.get_by_test_id("connection-edit-scope")).to_be_visible()
    expect(page.get_by_test_id("sync-history")).to_be_visible()
    expect(page.get_by_text(re.compile(r"Access", re.I)).first).to_be_visible()
    expect(page.get_by_text(re.compile(r"Daily at 02:00|daily at 02:00", re.I)).first).to_be_visible()
    shot("detail")

    # Scan now starts a scan and lands on the scan view
    page.get_by_test_id("connection-scan-now").click()
    expect(page).to_have_url(re.compile(rf"#/connections/{cid}/scans/[^/]+"), timeout=15000)
    expect(page.get_by_test_id("scan-view")).to_be_visible()
    page.goto(f"./#/connections/{cid}")
    expect(page.get_by_test_id("sync-history").get_by_role("row")).not_to_have_count(0)

    # Disconnect via the dialog: cancel keeps it, confirm disconnects but keeps agents
    page.get_by_test_id("connection-disconnect").click()
    dlg = page.get_by_test_id("disconnect-dialog")
    expect(dlg).to_be_visible()
    expect(dlg).to_contain_text(re.compile("stay", re.I))
    expect(dlg).to_contain_text(re.compile("Source disconnected", re.I))
    page.get_by_test_id("disconnect-cancel").click()
    expect(dlg).not_to_be_visible()
    page.get_by_test_id("connection-disconnect").click()
    page.get_by_test_id("disconnect-confirm").click()
    expect(page.get_by_test_id("connection-status")).to_contain_text(re.compile("Disconnected", re.I))
    expect(page.get_by_test_id("connection-reconnect")).to_be_visible()
    shot("disconnected")
    # agents are still in the fleet and marked "source disconnected"
    for aid in out["agent_ids"]:
        page.goto(f"./#/agents/{aid}")
        expect(page.get_by_text(re.compile("Source disconnected", re.I)).first).to_be_visible()
    page.goto("./#/")
    expect(page.get_by_test_id("fleet-row").filter(has_text=re.compile("Source disconnected", re.I)).first).to_be_visible()
    shot("then")


def test_po63_summary_counts_have_links_and_schedule_save(page, context, shot):
    cid = _verified(context, "microsoft")
    sid, _ = _scan(context, cid)
    new = _staged(context, sid, "new")
    page.goto(f"./#/connections/{cid}/scans/{sid}/review")
    expect(page.get_by_test_id("review-import")).to_be_enabled()
    page.get_by_test_id("review-import").click()
    expect(page.get_by_test_id("import-summary")).to_be_visible(timeout=15000)
    expect(page.get_by_test_id("import-summary-count-new")).to_contain_text(str(len(new)))
    for k in ("updated", "no-owner", "missing"):
        expect(page.get_by_test_id(f"import-summary-count-{k}")).to_be_visible()
    expect(page.get_by_test_id("summary-open-fleet")).to_be_visible()
    expect(page.get_by_test_id("summary-open-connection")).to_be_visible()
    page.get_by_test_id("sync-schedule-off").check()
    page.get_by_test_id("sync-save").click()
    expect(page.get_by_text(re.compile("Saved", re.I)).first).to_be_visible()
    got = context.request.get(f"api/connections/{cid}/schedule", headers=HEADERS).json()
    assert got["schedule"] == "off", got
    shot("then")
    page.get_by_test_id("summary-open-connection").click()
    expect(page).to_have_url(re.compile(rf"#/connections/{cid}$"))
    expect(page.get_by_test_id("connection-detail")).to_be_visible()


def test_po63_connection_detail_error_state(page, context, shot):
    page.route(re.compile(r".*/api/connections/[^/?]+$"), lambda route: route.fulfill(status=500, body="boom")
               if route.request.method == "GET" else route.continue_())
    page.goto("./#/connections/some-conn")
    expect(page.get_by_role("alert").first).to_be_visible()
    shot("then")


# ---- awaiting queue, single-agent intake, Fleet --------------------------------------------------------------------
def test_po64_awaiting_queue_on_detail_and_fleet_banner(page, context, shot):
    cid = _verified(context, "databricks")
    r = context.request.post(f"api/connections/{cid}/sync", headers=HEADERS)
    assert r.ok, r.text()
    aw = context.request.get(f"api/connections/{cid}/awaiting", headers=HEADERS).json()
    assert aw["count"] > 0 and len(aw["rows"]) == aw["count"], aw
    page.goto(f"./#/connections/{cid}")
    expect(page.get_by_test_id("connection-awaiting-count")).to_contain_text(str(aw["count"]))
    queue = page.get_by_test_id("awaiting-import-queue")
    expect(queue).to_be_visible()
    expect(page.get_by_test_id(re.compile(r"^awaiting-row-"))).to_have_count(aw["count"])
    expect(page.get_by_test_id("awaiting-review-all")).to_be_visible()
    shot("queue")
    # a row's Review opens the single-agent intake
    page.get_by_test_id(re.compile(r"^awaiting-review-\d+$")).first.click()
    expect(page).to_have_url(re.compile(rf"#/connections/{cid}/intake/[^/]+"))
    expect(page.get_by_test_id("intake-page")).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    expect(page.get_by_test_id("intake-import")).to_be_visible()
    expect(page.get_by_test_id("intake-dismiss")).to_be_visible()
    for c in CRITERIA:
        expect(page.get_by_test_id(f"intake-criterion-{c}")).to_be_visible()
    page.get_by_test_id("intake-dismiss").click()
    expect(page.get_by_test_id("intake-dismiss-reason")).to_be_visible()
    shot("intake")
    # Fleet banner while the queue is non-empty
    page.goto("./#/")
    banner = page.get_by_test_id("fleet-awaiting-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text(re.compile("awaiting import", re.I))
    shot("then")


def test_po64_fleet_header_actions(page, shot):
    page.goto("./#/")
    add = page.get_by_test_id("fleet-add-agents")
    expect(add).to_be_visible()
    expect(add).to_contain_text("Add agents")
    expect(page.get_by_test_id("register-external-button")).to_be_visible()
    expect(page.get_by_test_id("fleet-add-by-url")).to_be_visible()
    expect(page.get_by_test_id("register-external-button")).to_contain_text("Add by URL")
    page.get_by_test_id("register-external-button").click()
    expect(page).to_have_url(re.compile(r"#/(register|connections/add-by-url)"))
    page.goto("./#/")
    add.click()
    expect(page).to_have_url(re.compile(r"#/connections$"))
    expect(page.get_by_test_id("provider-gallery")).to_be_visible()
    shot("then")


# ---- PO-6.5 sample badge -------------------------------------------------------------------------------------------
def test_po65_sample_badge_on_review_detail_and_never_live(page, context, shot):
    cid = _verified(context, "snowflake")
    sid, _ = _scan(context, cid)
    page.goto(f"./#/connections/{cid}")
    expect(page.get_by_test_id("connection-detail")).to_be_visible()
    badge = page.get_by_test_id("sample-badge").first
    expect(badge).to_be_visible()
    expect(badge).to_contain_text("Sample data")
    expect(page.get_by_test_id("connection-status")).not_to_have_text(re.compile(r"^\s*Connected\s*$", re.I))
    _review(page, cid, sid)
    banner = page.get_by_test_id("sample-banner")
    expect(banner).to_have_attribute("role", "note")
    expect(banner).to_contain_text("Sample data")
    page.get_by_test_id("review-filter-new").click()
    for rid in _row_ids(page):
        expect(page.get_by_test_id(f"review-row-{rid}").get_by_test_id("sample-badge")).to_be_visible()
    expect(page.get_by_test_id("review-import")).to_contain_text("as sample data")
    shot("then")


# (last: imports the databricks sample agents, which the earlier databricks tests must not see as Unchanged)
def test_po64_agent_source_chip_links_to_connection(page, context, shot):
    cid = _verified(context, "databricks")
    sid, _ = _scan(context, cid)
    new = _staged(context, sid, "new")
    out = context.request.post(f"api/scans/{sid}/import", headers=HEADERS, data={"ids": [r["id"] for r in new]}).json()
    page.goto(f"./#/agents/{out['agent_ids'][0]}")
    chip = page.get_by_test_id("agent-source")
    expect(chip).to_be_visible()
    link = chip if (chip.evaluate("e => e.tagName") == "A") else chip.get_by_role("link")
    expect(link).to_have_attribute("href", re.compile(rf"#/connections/{cid}$"))
    expect(page.get_by_test_id("sample-badge").first).to_be_visible()
    link.click()
    expect(page).to_have_url(re.compile(rf"#/connections/{cid}$"))
    expect(page.get_by_test_id("connection-detail")).to_be_visible()
    shot("then")
