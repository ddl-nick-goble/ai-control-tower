"""T-9 console e2e: shared contract for the builder.

Routes (HashRouter, relative to APP_URL):
  #/                    Fleet
  #/agents/<agent_id>   Agent page (agent_id is the full id, e.g. act:aws:de78ef1edc1eaad4)
  #/runs/<run_id>       Run timeline

data-testid contract
  Fleet:
    fleet-row                (one per agent; data-agent-id=<id>; contains the agent name as a link/button)
    fleet-source, fleet-name, fleet-owner, fleet-status, fleet-last-seen   (cells inside fleet-row)
    tier-badge               (inside fleet-row and on the agent page; text contains "Tier <n>"; data-tier=<n>)
    open-findings            (inside fleet-row; text is the integer count)
    source-filter            (a <select>; options are source platforms, plus an "All" option with value "")
    tier-filter              (a <select>; options "1".."4", plus an "All" option with value "")
    register-external-button (button labelled "Register external agent")
  Agent page:
    tier-card                (contains tier-badge and reasoning text)
    criteria-item            (exactly 7, one per tier criterion)
    tier-reasoning
    identity-panel
    identity-flag-no-distinct (shown only when the agent has no distinct identity; text "no distinct identity")
    manifest-tool            (one per manifest tool)
    bundle-id                (text is the bundle id)
    policy-name              (one per policy on the bundle; inside the same governance panel as bundle-id)
    dependency-model         (one per model dependency; contains name and the registry version text)
    approvals-panel
    finding-item             (one per finding; data-finding-type, data-status; text has tool and timestamp)
    finding-timeline-link    (link inside finding-item to #/runs/<run_id>)
  Run timeline:
    timeline-event           (one per event in ts order; data-kind, data-span-id, data-violation="true"|"false";
                              text contains the event name)
    timeline-event-args      (inside tool_call timeline-event; the redacted arguments)
    timeline-page            (root element; has data-event-count once rendered)
"""
import json
import re
import time

import pytest
from playwright.sync_api import expect


def _open_agent(page, agent_id):
    page.goto(f"./#/agents/{agent_id}")


def _approvable(page):
    """kyc-refresh-agent: discovered, registered by seed-user, no governance bundle."""
    return next(a["id"] for a in page.request.get("./api/agents").json() if a["name"] == "kyc-refresh-agent")


def _fleet_row(page, name):
    return page.get_by_test_id("fleet-row").filter(has_text=name)


def test_ac5_timeline_matches_api_and_renders_fast(page, shot, seeded):
    run_id = seeded["run_id"]
    api = page.request.get(f"./api/runs/{run_id}/timeline")
    assert api.ok
    events = api.json()["events"]
    assert 0 < len(events) <= 500

    t0 = time.monotonic()
    page.goto(f"./#/runs/{run_id}")
    items = page.get_by_test_id("timeline-event")
    expect(items).to_have_count(len(events), timeout=2000)
    assert time.monotonic() - t0 < 2.0, "timeline took longer than 2 s to render"

    kinds = [i.get_attribute("data-kind") for i in items.all()]
    assert kinds == [e["kind"] for e in events]
    spans = [i.get_attribute("data-span-id") for i in items.all()]
    assert spans == [e.get("span_id") for e in events]
    # names appear in ts order
    for item, ev in zip(items.all(), events):
        if ev["kind"] == "model_call":
            expect(item).to_have_attribute("data-kind", "model_call")
            label = item.inner_text().strip()
            assert label, "model_call event must show a non-empty label"
            assert ev["name"] not in item.inner_html()
            assert ev["name"] not in label
        else:
            expect(item).to_contain_text(ev["name"])
    # tool-call arguments are shown
    transfer = items.filter(has_text="transfer_funds")
    expect(transfer.get_by_test_id("timeline-event-args")).to_contain_text("4750000")
    expect(transfer.get_by_test_id("timeline-event-args")).to_contain_text("CONC-USD-100")
    shot("then")


def test_ac6_scope_drift_on_agent_page_and_timeline_violations(page, shot, seeded):
    _open_agent(page, seeded["treasury_id"])
    # select the weekend-drift finding by timestamp
    finding = page.get_by_test_id("finding-item").filter(has_text="2026-11-07 02:14")
    expect(finding).to_have_count(1)
    expect(finding).to_have_attribute("data-finding-type", "scope_drift")
    expect(finding).to_have_attribute("data-status", "open")
    expect(finding).to_contain_text("2026-11-07 02:14")
    expect(finding).to_contain_text("UTC")

    shot("agent-page")
    finding.get_by_test_id("finding-timeline-link").click()
    items = page.get_by_test_id("timeline-event")
    expect(items.first).to_be_visible()
    for tool in ("list_credentials", "transfer_funds"):
        ev = items.filter(has_text=tool)
        expect(ev).to_have_count(1)
        expect(ev).to_have_attribute("data-violation", "true")
    expect(items.filter(has_text="sweep_plan")).to_have_attribute("data-violation", "false")
    shot("then")


def test_ac8_recon_agent_both_policies_on_one_bundle_and_model_dependency(page, shot, seeded):
    _open_agent(page, seeded["recon_id"])
    bundle = page.get_by_test_id("bundle-id")
    expect(bundle).to_have_count(1)
    expect(bundle).not_to_be_empty()
    policies = page.get_by_test_id("policy-name")
    expect(policies).to_have_count(2)
    expect(policies.filter(has_text="ACT 2026-54 Agentic Governance")).to_have_count(1)
    expect(policies.filter(has_text="SR 26-2 Model Risk (slim)")).to_have_count(1)
    dep = page.get_by_test_id("dependency-model").filter(has_text="break_classifier")
    expect(dep).to_have_count(1)
    expect(dep).to_contain_text(re.compile(r"\b5\b"))
    shot("then")


def test_ac12_fleet_tier1_badge_one_open_finding_links_to_timeline(page, shot, seeded):
    page.goto("./#/")
    row = _fleet_row(page, "treasury-sweep-agent")
    expect(row).to_have_count(1)
    expect(row.get_by_test_id("tier-badge")).to_contain_text("Tier 1")
    expect(row.get_by_test_id("open-findings")).to_have_text("1")

    row.get_by_role("link", name="treasury-sweep-agent").click()
    page.get_by_test_id("finding-item").filter(has_text="2026-11-07 02:14").get_by_test_id(
        "finding-timeline-link").click()
    expect(page).to_have_url(re.compile(rf"#/runs/{seeded['run_id']}$"))
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()
    shot("then")


def test_fleet_columns_filters_and_register_button(page, shot, seeded):
    page.goto("./#/")
    all_rows = page.get_by_test_id("fleet-row")
    ext = re.compile(r"^\s*external\s*$", re.I)
    # Order-independent: external agents registered by other modules may be present.
    rows = all_rows.filter(has_not=page.get_by_test_id("fleet-source").filter(has_text=ext))
    expect(rows).to_have_count(12)
    n_ext = all_rows.count() - 12
    expect(all_rows.filter(has=page.get_by_test_id("fleet-source").filter(has_text=ext))).to_have_count(n_ext)
    for col in ("Source", "Name", "Owner", "Tier", "Status", "Open findings", "Last seen"):
        expect(page.get_by_role("columnheader", name=re.compile(col, re.I))).to_be_visible()
    first = all_rows.first
    for tid in ("fleet-source", "fleet-name", "fleet-owner", "tier-badge", "fleet-status", "open-findings",
                "fleet-last-seen"):
        expect(first.get_by_test_id(tid)).to_be_visible()
    expect(_fleet_row(page, "recon-agent")).to_have_count(1)
    expect(page.get_by_role("button", name="Register external agent")).to_be_visible()
    expect(page.get_by_test_id("register-external-button")).to_be_visible()

    page.get_by_test_id("source-filter").select_option("aws")
    expect(rows).to_have_count(3)
    page.get_by_test_id("source-filter").select_option("")
    expect(rows).to_have_count(12)
    expect(all_rows).to_have_count(12 + n_ext)
    page.get_by_test_id("tier-filter").select_option("1")
    expect(rows).to_have_count(2)  # treasury-sweep-agent, payments-fraud-screener
    for r in rows.all():
        expect(r.get_by_test_id("tier-badge")).to_contain_text("Tier 1")
    shot("then")


def test_agent_page_tier_card_identity_manifest_approvals(page, shot, seeded):
    _open_agent(page, seeded["treasury_id"])
    card = page.get_by_test_id("tier-card")
    expect(card.get_by_test_id("tier-badge")).to_contain_text("Tier 1")
    expect(card.get_by_test_id("criteria-item")).to_have_count(7)
    expect(card.get_by_test_id("tier-reasoning")).not_to_be_empty()
    expect(page.get_by_test_id("identity-panel")).to_be_visible()
    expect(page.get_by_test_id("manifest-tool").filter(has_text="transfer_funds")).to_have_count(1)
    expect(page.get_by_test_id("approvals-panel")).to_be_visible()

    # an agent with no distinct identity shows the flag
    api = page.request.get("./api/agents").json()
    flagged = []
    for a in api:
        rec = page.request.get(f"./api/agents/{a['id']}").json()
        if (rec.get("identity") or {}).get("distinct") is False:
            flagged.append(a["id"])
    assert flagged, "seed fleet should include at least one agent with no distinct identity"
    _open_agent(page, flagged[0])
    expect(page.get_by_test_id("identity-flag-no-distinct")).to_contain_text(re.compile("no distinct identity", re.I))
    shot("then")


@pytest.mark.parametrize("route", ["./#/", "AGENT", "RUN"])
def test_no_page_mentions_claude(page, shot, seeded, route):
    name = {"./#/": "fleet", "AGENT": "agent", "RUN": "run"}[route]
    if route == "AGENT":
        route = f"./#/agents/{seeded['treasury_id']}"
    elif route == "RUN":
        route = f"./#/runs/{seeded['run_id']}"
    page.goto(route)
    expect(page.get_by_role("heading").first).to_be_visible()
    assert "claude" not in page.locator("body").inner_text().lower()
    shot(f"then-{name}")


MODEL_WORDS = ("claude", "anthropic", "sonnet", "haiku", "opus")


def test_no_model_ids_leak_on_any_event_kind(page, shot, seeded):
    page.goto(f"./#/runs/{seeded['run2_id']}")
    items = page.get_by_test_id("timeline-event")
    expect(items.first).to_be_visible()
    kinds = {i.get_attribute("data-kind") for i in items.all()}
    assert len(kinds) >= 3, kinds
    text = page.locator("body").inner_text().lower()
    for w in MODEL_WORDS:
        assert w not in text, f"{w!r} visible on page"
    for i in items.all():
        html = i.inner_html().lower()
        for w in MODEL_WORDS:
            assert w not in html, f"{w!r} in {i.get_attribute('data-kind')} event: {html[:200]}"
    shot("then")


def test_violation_labels_distinguish_reasons(page, shot, seeded):
    page.goto(f"./#/runs/{seeded['run2_id']}")
    items = page.get_by_test_id("timeline-event")
    expect(items.first).to_be_visible()
    unv = items.filter(has_text="propose_adjustment")
    expect(unv).to_have_count(1)
    expect(unv).to_have_attribute("data-violation", "true")
    expect(unv).to_have_attribute("data-violation-reason", "value_unverifiable")
    expect(unv).to_contain_text("Unverifiable value")
    expect(unv).not_to_contain_text("Outside manifest")
    shot("then-unverifiable")

    page.goto(f"./#/runs/{seeded['run_id']}")
    items = page.get_by_test_id("timeline-event")
    expect(items.first).to_be_visible()
    for tool, reason, label in (("list_credentials", "tool_not_in_manifest", "Outside manifest"),
                                ("transfer_funds", "value_limit_exceeded", "Over limit")):
        ev = items.filter(has_text=tool)
        expect(ev).to_have_count(1)
        expect(ev).to_have_attribute("data-violation", "true")
        expect(ev).to_have_attribute("data-violation-reason", reason)
        expect(ev).to_contain_text(label)
    shot("then")


def test_approve_by_registrant_is_refused_visibly(page, context, shot, seeded):
    context.set_extra_http_headers({"X-Domino-User-Id": "seed-user", "X-Domino-User": "seed-user"})
    _open_agent(page, seeded["recon_id"])  # registered by seed-user; the app runs as seed-user
    page.get_by_role("button", name="Approve").click()
    err = page.get_by_test_id("approve-error")
    expect(err).to_be_visible()
    expect(err).to_contain_text(re.compile(r"registered.*cannot approve|cannot approve", re.I))
    expect(page.get_by_test_id("approve-success")).to_have_count(0)
    shot("then")


def test_approve_by_other_user_shows_success_and_expiry(page, context, shot, seeded):
    context.set_extra_http_headers({"X-Domino-User-Id": "approver-2", "X-Domino-User": "approver-2"})
    _open_agent(page, _approvable(page))
    expect(page.get_by_test_id("agent-status")).to_contain_text(re.compile("discovered|pending review", re.I))
    page.get_by_role("button", name="Approve").click()
    ok = page.get_by_test_id("approve-success")
    expect(ok).to_be_visible()
    expect(ok).to_contain_text("Approved")
    expect(ok).to_contain_text(re.compile(r"20\d\d-\d\d-\d\d"))
    expect(page.get_by_test_id("agent-status")).to_have_text(re.compile("^approved$", re.I))
    shot("then")


def test_governance_labels_expected_vs_attached(page, shot, seeded):
    # agent with no bundle
    _open_agent(page, _approvable(page))
    expect(page.get_by_test_id("bundle-id")).to_contain_text("not yet created")
    expect(page.get_by_test_id("policies-expected")).to_be_visible()
    expect(page.get_by_test_id("policies-attached")).to_have_count(0)
    shot("then-no-bundle")
    _open_agent(page, seeded["recon_id"])
    expect(page.get_by_test_id("policies-attached")).to_be_visible()
    expect(page.get_by_test_id("policies-expected")).to_have_count(0)
    shot("then")


def test_identity_panel_flag_and_principal(page, shot, seeded):
    agents = {a["name"]: a["id"] for a in page.request.get("./api/agents").json()}
    flagged = []
    for name, aid in agents.items():
        ident = page.request.get(f"./api/agents/{aid}").json().get("identity") or {}
        if not ident or ident.get("type") in (None, "none"):
            flagged.append(aid)
    assert flagged, "seed has agents with missing/none identity"
    _open_agent(page, flagged[0])
    expect(page.get_by_test_id("identity-flag-no-distinct")).to_be_visible()
    shot("then-flagged")
    _open_agent(page, seeded["recon_id"])
    panel = page.get_by_test_id("identity-panel")
    expect(panel).to_contain_text("svc-act-recon")
    expect(panel.get_by_test_id("identity-flag-no-distinct")).to_have_count(0)
    shot("then")


def test_timeline_input_output_events_show_content(page, shot, seeded):
    run_id = seeded["run_id"]
    events = page.request.get(f"./api/runs/{run_id}/timeline").json()["events"]
    keys = ("gen_ai.input.messages", "gen_ai.output.messages", "content")
    expected = [e for e in events if e["kind"] in ("input", "output")
                and (e.get("content") or any((e.get("attrs") or {}).get(k) for k in keys))]
    assert expected, "API should provide content for at least one input/output event"
    page.goto(f"./#/runs/{run_id}")
    expect(page.get_by_test_id("timeline-event").first).to_be_visible()
    for kind in sorted({e["kind"] for e in expected}):
        ev = page.locator(f"[data-testid='timeline-event'][data-kind='{kind}']").first
        content = ev.get_by_test_id("timeline-event-content")
        expect(content).to_be_visible()
        expect(content).not_to_be_empty()
    shot("then")
