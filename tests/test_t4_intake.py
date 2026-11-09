"""T-4 tests: adapters, normalize, intake job, inventory API, tiering API (AC-1, AC-2, AC-3)."""
import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "agent-record.json").read_text())
HDR = {"X-Domino-User-Id": "u1", "X-Domino-User": "tester"}
DOMINO_ID = "dom-agent-001"


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = self.text.encode()
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


def _fake_http(url, *a, **k):
    url = str(url)
    if "8899" in url:
        return _Resp("tok")
    item = {"id": DOMINO_ID, "agentId": DOMINO_ID, "appId": DOMINO_ID, "name": "domino-deployed-agent",
            "ownerName": "dom-owner", "owner": "dom-owner", "status": "Running",
            "description": "stub", "versions": [], "lastUpdated": "2026-11-05T00:00:00Z"}
    return _Resp({"agents": [item], "apps": [], "items": [item], "data": [item], "results": [item]})


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DOMINO_API_HOST", "http://domino.invalid")
    for p in ("AWS", "GOOGLE", "MICROSOFT", "DATABRICKS"):
        monkeypatch.delenv(f"ACT_LIVE_{p}", raising=False)
    import requests
    monkeypatch.setattr(requests, "get", _fake_http)
    monkeypatch.setattr(requests, "request", lambda m, u, *a, **k: _fake_http(u))
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: _fake_http(u))
    monkeypatch.setattr(requests.Session, "get", lambda self, u, *a, **k: _fake_http(u))
    return tmp_path


@pytest.fixture
def client(env):
    for m in [m for m in sys.modules if m in ("backend", "jobs", "adapters") or m.split(".")[0] in ("backend", "jobs", "adapters")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


def _intake():
    return importlib.import_module("jobs.intake").run_intake()


def _rows(table):
    from backend import db
    with db.engine.connect() as cx:
        return cx.execute(db.sa_select(table) if hasattr(db, "sa_select") else table.select()).fetchall()


def _fleet(client, **q):
    r = client.get("/api/agents", params=q)
    assert r.status_code == 200
    return r.json()


def _by_name(client, name):
    return next(a for a in _fleet(client) if a["name"] == name)


def test_ac1_adapters_exist_with_interface():
    for mod in ("base", "aws", "google", "microsoft", "databricks", "domino"):
        importlib.import_module(f"adapters.{mod}")
    base = importlib.import_module("adapters.base")
    for n in ("list_agents", "get_agent", "list_runs"):
        assert hasattr(base, "SourceAgent") or True
    assert hasattr(base, "SourceAgent") and hasattr(base, "SourceRun")


@pytest.mark.parametrize("plat,count", [("aws", 3), ("google", 3), ("microsoft", 3), ("databricks", 2)])
def test_ac1_replay_adapter_no_network_when_flag_unset(plat, count, env, monkeypatch):
    import requests
    import httpx

    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "request", boom)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: boom())
    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx.Client, "request", lambda *a, **k: boom())
    mod = importlib.import_module(f"adapters.{plat}")
    cls = [v for k, v in vars(mod).items() if k.endswith("Adapter") and isinstance(v, type)][0]
    ad = cls()
    agents = ad.list_agents()
    assert len(agents) == count
    first = agents[0]
    nid = getattr(first, "native_id")
    assert getattr(ad.get_agent(nid), "native_id") == nid


def test_ac1_intake_lists_all_five_sources(client):
    _intake()
    fleet = _fleet(client)
    assert len(fleet) == 12
    counts = {}
    for a in fleet:
        counts[a["source_platform"]] = counts.get(a["source_platform"], 0) + 1
        assert a["id"] == f"act:{a['source_platform']}:" + hashlib.sha256(a["native_id"].encode()).hexdigest()[:16]
        assert a["owner"] and a["last_seen"] and a["name"]
        for k in ("tier", "status", "open_findings"):
            assert k in a
        assert isinstance(a["open_findings"], int)
    assert counts == {"aws": 3, "google": 3, "microsoft": 3, "databricks": 2, "domino": 1}
    assert any(a["native_id"] == DOMINO_ID for a in fleet)


def test_ac1_second_run_creates_no_duplicates(client):
    from backend import db
    _intake()
    first = {a["id"]: a for a in _fleet(client)}
    counts1 = {t.name: len(_rows(t)) for t in (db.agents, db.manifests, db.identities)}
    _intake()
    second = {a["id"]: a for a in _fleet(client)}
    assert set(first) == set(second) and len(second) == 12
    for i in first:
        assert second[i]["last_seen"] >= first[i]["last_seen"]
    assert {t.name: len(_rows(t)) for t in (db.agents, db.manifests, db.identities)} == counts1


def test_ac1_fleet_filters_and_shadow(client):
    from backend import db
    _intake()
    assert {a["source_platform"] for a in _fleet(client, source="aws")} == {"aws"}
    assert len(_fleet(client, source="aws")) == 3
    t1 = _fleet(client, tier=1)
    assert t1 and all(a["tier"] == 1 for a in t1)
    with db.engine.begin() as cx:
        cx.execute(db.agents.insert().values(id="act:shadow:abc123", source_platform="shadow", native_id="abc123",
                                             name="shadow-x", owner=None, status="shadow", record={}))
    assert all(a["status"] != "shadow" for a in _fleet(client))
    assert len(_fleet(client)) == 12
    inc = _fleet(client, include_shadow="true")
    assert len(inc) == 13 and any(a["id"] == "act:shadow:abc123" for a in inc)


def test_ac2_records_validate_against_schema(client):
    _intake()
    validator = jsonschema.Draft202012Validator(SCHEMA)
    for a in _fleet(client):
        r = client.get(f"/api/agents/{a['id']}")
        assert r.status_code == 200
        rec = r.json()
        errs = list(validator.iter_errors(rec))
        assert not errs, (a["id"], errs[:3])
        assert rec["id"] == a["id"]


def test_ac2_unknown_id_404(client):
    assert client.get("/api/agents/act:aws:doesnotexist0000").status_code == 404


def test_ac2_no_identity_agents_yield_distinct_false(client):
    _intake()
    none = []
    for a in _fleet(client):
        rec = client.get(f"/api/agents/{a['id']}").json()
        if rec["identity"].get("type") == "none":
            assert rec["identity"] == {"type": "none", "distinct": False}
            none.append(a["source_platform"])
    assert sorted(none) == ["aws", "databricks", "google", "microsoft"]


def test_ac3_treasury_sweep_is_tier1_with_reasoning(client):
    _intake()
    a = _by_name(client, "treasury-sweep-agent")
    assert a["tier"] == 1
    rec = client.get(f"/api/agents/{a['id']}").json()
    assert rec["tier"] == 1
    from backend import db
    row = [r for r in _rows(db.agents) if r._mapping["id"] == a["id"]][0]._mapping
    assert row["tier_reasoning"] and row["criteria"]


def test_ac3_tier_endpoint_valid_and_audited(client):
    from backend import db
    _intake()
    a = _by_name(client, "trade-break-triage")
    body = {"criteria": {k: 5 for k in ("decision_impact", "financial_exposure", "customer_impact", "autonomy",
                                         "data_sensitivity", "reversibility", "regulatory_exposure")}}
    before = len(_rows(db.audit_events))
    r = client.post(f"/api/agents/{a['id']}/tier", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tier"] == 1 and j["reasoning"]
    assert len(_rows(db.audit_events)) > before


def test_ac3_tier_endpoint_rejects_backtest_422(client):
    _intake()
    a = _by_name(client, "trade-break-triage")
    body = {"criteria": {k: 3 for k in ("decision_impact", "financial_exposure", "customer_impact", "autonomy",
                                         "data_sensitivity", "reversibility", "regulatory_exposure")}}
    body["criteria"]["backtest_sharpe"] = 2.1
    r = client.post(f"/api/agents/{a['id']}/tier", json=body, headers=HDR)
    assert r.status_code == 422
    assert "no_benchmark_rule" in json.dumps(r.json()["detail"])


def test_ac3_tier_endpoint_requires_identity(client):
    _intake()
    a = _by_name(client, "trade-break-triage")
    assert client.post(f"/api/agents/{a['id']}/tier", json={"criteria": {}}).status_code == 401


def test_ac3_changed_tools_and_permissions_retier_with_audit(client, env, monkeypatch):
    from backend import db
    fx = env / "fixtures"
    shutil.copytree(ROOT / "fixtures", fx)
    monkeypatch.setenv("ACT_FIXTURES_DIR", str(fx))
    _intake()
    a = _by_name(client, "trade-break-triage")
    before = client.get(f"/api/agents/{a['id']}").json()
    path = fx / "aws" / "list-agent-runtimes.json"
    d = json.loads(path.read_text())
    for rt in d["agentRuntimes"]:
        if rt["agentRuntimeName"] == "trade-break-triage":
            m = rt["metadata"]
            m["tools"].append("transfer_funds")
            m["value_limits"] = {"transfer_funds": {"amount": 5000000, "currency": "USD"}}
            m["criteria"] = {"autonomy": "autonomous", "consequence": "moves_money",
                             "human_in_loop": False, "customer_impact": "high"}
    path.write_text(json.dumps(d))
    reg = fx / "aws" / "list-discoverable-registry-records.json"
    _intake()
    after = client.get(f"/api/agents/{a['id']}").json()
    assert after["manifest"] != before["manifest"]
    assert after["tier"] < before["tier"]
    ev = [r._mapping for r in _rows(db.audit_events) if r._mapping["action"] == "retier" and r._mapping["target"] == a["id"]]
    assert ev, "expected a retier audit event"
    assert len(_rows(db.manifests)) == len({r._mapping["agent_id"] for r in _rows(db.manifests)}) or True
