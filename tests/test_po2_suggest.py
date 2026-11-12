"""PO-2 tests: suggested criteria scores (backend/suggest.py, POST /api/suggest/*).

Rules file override: tests point the scorer at a temp rules file through the env var ACT_CRITERIA_RULES
(path to a criteria_rules.yaml). Default is config/criteria_rules.yaml.
"""
import copy
import importlib
import pathlib
import sys

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES = ROOT / "config" / "criteria_rules.yaml"
HDR = {"X-Domino-User-Id": "po2", "X-Domino-User": "po2-tester"}
CRITERIA = ["decision_impact", "financial_exposure", "customer_impact", "autonomy",
            "data_sensitivity", "reversibility", "regulatory_exposure"]


def _fresh(db_env, monkeypatch):
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


@pytest.fixture
def suggest(db_env, monkeypatch):
    _fresh(db_env, monkeypatch)
    return importlib.import_module("backend.suggest")


@pytest.fixture
def client(db_env, monkeypatch):
    _fresh(db_env, monkeypatch)
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


TREASURY = {
    "name": "treasury-sweep-agent",
    "description": "Sweeps excess cash between operating accounts.",
    "tools": ["read_balances", "transfer_funds", "notify_ops"],
    "data_scopes": ["treasury.accounts:write", "customer.accounts:write"],
    "systems": ["TMS", "core-banking"],
    "value_limits": {"transfer_funds": {"amount": 5000000, "currency": "USD"}},
    "identity": {"type": "service_principal", "principal": "sp-sweep", "distinct": True},
    "dependencies": {"models": [], "mcp_servers": []},
    "extra": {"human_in_loop": False},
}
MINIMAL = {
    "name": "doc-summarizer",
    "description": "Summarizes public documents.",
    "tools": ["read_document", "search_docs"],
    "data_scopes": ["public.docs:read"],
    "systems": [],
    "value_limits": {},
    "identity": {"type": "user", "principal": "u", "distinct": False},
    "dependencies": {},
    "extra": {"human_in_loop": True},
}


def test_ac1_all_seven_present_int_range_single_line_reason(suggest):
    out = suggest.suggest_scores(TREASURY)
    assert set(out) == set(CRITERIA)
    for k, v in out.items():
        assert isinstance(v["score"], int) and not isinstance(v["score"], bool)
        assert 1 <= v["score"] <= 5
        assert isinstance(v["reason"], str) and v["reason"].strip()
        assert "\n" not in v["reason"]
        assert isinstance(v["evidence"], str)


def test_ac1_reason_cites_evidence(suggest):
    out = suggest.suggest_scores(TREASURY)
    for k, v in out.items():
        if v["reason"] == "no evidence found":
            continue
        assert v["evidence"].strip(), k
        assert v["evidence"] in v["reason"], k


def test_ac1_payment_tool_with_limit_drives_financial_exposure(suggest):
    v = suggest.suggest_scores(TREASURY)["financial_exposure"]
    assert v["score"] >= 4
    assert "transfer_funds" in v["reason"]


def test_ac1_no_human_approval_gives_high_autonomy(suggest):
    agent = copy.deepcopy(TREASURY)
    agent["tools"] = ["read_balances", "transfer_funds"]
    v = suggest.suggest_scores(agent)["autonomy"]
    assert v["score"] >= 4


def test_ac1_write_scope_on_customer_pii_gives_high_data_sensitivity(suggest):
    agent = copy.deepcopy(MINIMAL)
    agent["data_scopes"] = ["customer.pii:write"]
    v = suggest.suggest_scores(agent)["data_sensitivity"]
    assert v["score"] >= 4
    assert "customer.pii" in v["reason"]


def test_ac1_minimal_read_only_agent_is_low_tier3_or_4(suggest, db_env):
    out = suggest.suggest_scores(MINIMAL)
    assert all(v["score"] <= 2 for v in out.values()), {k: v["score"] for k, v in out.items()}
    tiering = importlib.import_module("backend.tiering")
    r = tiering.score_tier({k: v["score"] for k, v in out.items()})
    assert r.tier in (3, 4)


def test_ac2_deterministic_across_calls(suggest):
    assert suggest.suggest_scores(TREASURY) == suggest.suggest_scores(copy.deepcopy(TREASURY))


def test_ac2_deterministic_across_reordered_tools(suggest):
    a = copy.deepcopy(TREASURY)
    b = copy.deepcopy(TREASURY)
    b["tools"] = list(reversed(b["tools"]))
    b["data_scopes"] = list(reversed(b["data_scopes"]))
    assert suggest.suggest_scores(a) == suggest.suggest_scores(b)


def test_ac2_tools_as_dicts_supported(suggest):
    agent = copy.deepcopy(TREASURY)
    agent["tools"] = [{"name": t} for t in agent["tools"]]
    out = suggest.suggest_scores(agent)
    assert out["financial_exposure"]["score"] >= 4


@pytest.mark.parametrize("agent", [{}, {"name": "x"}, {"name": "x", "tools": None, "data_scopes": None,
                                                       "value_limits": None, "identity": None, "extra": None},
                                   {"name": "x", "mystery_field": {"a": 1}}])
def test_ac1_unknown_or_missing_fields_do_not_crash(suggest, agent):
    out = suggest.suggest_scores(agent)
    assert set(out) == set(CRITERIA)
    for v in out.values():
        assert 1 <= v["score"] <= 5
        r = v["reason"]
        assert isinstance(r, str) and r.strip()
        assert r.strip().lower() != "no evidence found"
        assert "\n" not in r


def test_ac4_rules_file_exists_with_all_criteria():
    assert RULES.exists()
    text = RULES.read_text()
    for c in CRITERIA:
        assert c in text


def test_ac4_rules_file_is_the_source(db_env, monkeypatch, tmp_path):
    """Points ACT_CRITERIA_RULES at a temp copy with every default/score value raised to 5."""
    assert RULES.exists(), "config/criteria_rules.yaml missing"
    _fresh(db_env, monkeypatch)
    base_mod = importlib.import_module("backend.suggest")
    base = base_mod.suggest_scores(MINIMAL)

    def bump(o):
        if isinstance(o, dict):
            return {k: (5 if k in ("score", "default", "default_score") and isinstance(v, int) else bump(v))
                    for k, v in o.items()}
        if isinstance(o, list):
            return [bump(i) for i in o]
        return o
    changed = bump(yaml.safe_load(RULES.read_text()))
    p = tmp_path / "criteria_rules.yaml"
    p.write_text(yaml.safe_dump(changed))
    monkeypatch.setenv("ACT_CRITERIA_RULES", str(p))
    _fresh(db_env, monkeypatch)
    mod = importlib.import_module("backend.suggest")
    out = mod.suggest_scores(MINIMAL)
    assert out != base
    assert all(v["score"] == 5 for v in out.values())


def test_ac_route_suggest_scores(client):
    r = client.post("/api/suggest/scores", json={"agent": TREASURY}, headers=HDR)
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b["suggestions"]) == set(CRITERIA)
    assert isinstance(b["tier"], int) and 1 <= b["tier"] <= 4
    tiering = importlib.import_module("backend.tiering")
    exp = tiering.score_tier({k: v["score"] for k, v in b["suggestions"].items()})
    assert b["tier"] == exp.tier
    assert b["weighted"] == pytest.approx(exp.score)


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_ac3_override_422_without_reason(client, reason):
    body = {"agent_ref": "treasury-sweep-agent", "criterion": "autonomy", "score": 2}
    if reason is not None:
        body["reason"] = reason
    r = client.post("/api/suggest/override", json={**body, "agent": TREASURY}, headers=HDR)
    assert r.status_code == 422, r.text


def test_ac3_override_with_reason_writes_audit_and_updates(client):
    body = {"agent_ref": "treasury-sweep-agent", "agent": TREASURY, "criterion": "autonomy",
            "score": 1, "reason": "A human approves every sweep outside this tool"}
    r = client.post("/api/suggest/override", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["suggestions"]["autonomy"]["score"] == 1
    assert "override" in b["suggestions"]["autonomy"]["reason"].lower()
    assert "tier" in b
    db = importlib.import_module("backend.db")
    with db.engine.connect() as cx:
        rows = cx.execute(select(db.audit_events).where(
            db.audit_events.c.action == "criteria_override")).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_username == "po2-tester"
    assert "A human approves every sweep" in str(row.detail)
    assert "treasury-sweep-agent" in (str(row.target) + str(row.detail))


def test_ac_no_benchmark_rule_still_rejects(db_env):
    tiering = importlib.import_module("backend.tiering")
    crit = {k: 3 for k in CRITERIA}
    crit["backtest_sharpe"] = 4
    with pytest.raises(tiering.TieringInputRejected):
        tiering.score_tier(crit)
    crit = {k: 3 for k in CRITERIA}
    crit["benchmark_score"] = 4
    with pytest.raises(tiering.TieringInputRejected):
        tiering.score_tier(crit)


# ---- Review round 1: real seeded fixture agents -------------------------------------------------
import dataclasses

READ_ONLY_FIXTURE = "forecast"   # name fragment; resolved against the adapter output below


def _fixture_agents():
    from adapters.aws import AwsAdapter
    from adapters.databricks import DatabricksAdapter
    from adapters.google import GoogleAdapter
    from adapters.microsoft import MicrosoftAdapter
    out = []
    for ad in (AwsAdapter(), GoogleAdapter(), MicrosoftAdapter(), DatabricksAdapter()):
        out += [dataclasses.asdict(sa) for sa in ad.list_agents()]
    return out


def _fixture(name):
    return next(a for a in _fixture_agents() if a["name"] == name)


def test_ac1_real_fixture_treasury_sweep_is_tier_1_or_2(client):
    a = _fixture("treasury-sweep-agent")
    r = client.post("/api/suggest/scores", json={"agent": a}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["tier"] in (1, 2), r.json()


def test_ac1_real_fixture_liquidity_forecast_agent_is_tier_3_or_4(client):
    # liquidity-forecast-agent (databricks): query_cash_flows / run_forecast_model / publish_forecast, no value
    # limits, no write scopes, internal_report consequence. Clearly a read-only reporting/forecast agent.
    a = _fixture("liquidity-forecast-agent")
    r = client.post("/api/suggest/scores", json={"agent": a}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["tier"] in (3, 4), r.json()


def test_ac1_every_fixture_agent_gets_seven_scores_no_crash(suggest):
    agents = _fixture_agents()
    assert len(agents) >= 10
    for a in agents:
        out = suggest.suggest_scores(a)
        assert set(out) == set(CRITERIA), a["name"]
        assert all(1 <= v["score"] <= 5 and v["reason"] for v in out.values()), a["name"]


# ---- stored-record shape ----------------------------------------------------------------------
def _stored(client, name):
    from jobs.intake import run_intake
    run_intake()
    rows = client.get("/api/agents", headers=HDR).json()
    rows = rows.get("agents", rows) if isinstance(rows, dict) else rows
    aid = next(x["id"] for x in rows if x["name"] == name)
    r = client.get(f"/api/agents/{aid}", headers=HDR)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("name", ["treasury-sweep-agent"])
def test_ac2_stored_record_scores_equal_flat_source_agent(client, suggest, name):
    flat = suggest.suggest_scores(_fixture(name))
    stored = _stored(client, name)
    assert "manifest" in stored
    assert suggest.suggest_scores(stored) == flat
    r = client.post("/api/suggest/scores", json={"agent": stored}, headers=HDR)
    assert r.status_code == 200, r.text
    assert {k: v["score"] for k, v in r.json()["suggestions"].items()} == {k: v["score"] for k, v in flat.items()}


def test_ac2_stored_record_every_seeded_agent_matches_flat(client, suggest):
    from jobs.intake import run_intake
    run_intake()
    for a in _fixture_agents():
        stored = _stored(client, a["name"])
        assert suggest.suggest_scores(stored) == suggest.suggest_scores(a), a["name"]


# ---- human_in_loop ----------------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["criteria", "manifest_permissions", "extra"])
def test_ac1_human_in_loop_true_does_not_get_autonomy_4(suggest, shape):
    # transfer_funds would trigger the "no human approval" autonomy-4 rule if hitl were read as false/missing
    tools = ["read_balances", "transfer_funds"]
    base = {"name": "x", "tools": tools, "data_scopes": [], "systems": [], "value_limits": {}}
    if shape == "criteria":
        a = {**base, "criteria": {"human_in_loop": True}}
    elif shape == "manifest_permissions":
        a = {"name": "x", "manifest": {"tools": tools, "data_scopes": [], "systems": [], "value_limits": {},
                                       "permissions": {"human_in_loop": True}}}
    else:
        a = {**base, "extra": {"human_in_loop": True}}
    assert suggest.suggest_scores(a)["autonomy"]["score"] < 4


# ---- read verb first ---------------------------------------------------------------------------
def test_ac1_read_transfer_history_not_higher_than_read_only_tool(suggest):
    def mk(tool):
        return {"name": "x", "tools": [tool], "data_scopes": [], "systems": [], "value_limits": {}}
    base = suggest.suggest_scores(mk("read_document"))
    got = suggest.suggest_scores(mk("read_transfer_history"))
    for c in ("financial_exposure", "reversibility"):
        assert got[c]["score"] <= base[c]["score"], (c, got[c])


# ---- reasons for no-evidence criteria ---------------------------------------------------------
def test_ac1_no_evidence_reason_names_what_was_checked(suggest):
    out = suggest.suggest_scores({"name": "x", "tools": [], "data_scopes": [], "systems": [], "value_limits": {}})
    for c, v in out.items():
        r = v["reason"]
        assert r.strip().lower() != "no evidence found", c
        assert len(r) > 20, (c, r)
        assert "no" in r.lower().split() or "no " in r.lower(), (c, r)
        assert any(w in r.lower() for w in ("tool", "scope", "system", "limit", "setting", "identity", "human", "checked", "looked")), (c, r)
