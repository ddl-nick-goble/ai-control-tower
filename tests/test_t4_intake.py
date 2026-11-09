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


STATE = {}
OTHER_ID = "dom-agent-other"


def _item(nid, name, pub, owner):
    return {"id": nid, "agentId": nid, "appId": nid, "name": name,
            "ownerName": owner, "owner": owner, "status": "Running",
            "description": "stub", "versions": [], "lastUpdated": "2026-11-05T00:00:00Z",
            "currentVersion": {"versionNumber": 1, "currentInstance": {"publisher": {"name": pub}}}}


def _fake_http(url, *a, **k):
    url = str(url)
    if "8899" in url:
        return _Resp("tok")
    if url.endswith("/api/apps/v1/apps"):
        params = dict(k.get("params") or {})
        STATE.setdefault("calls", []).append(params)
        if STATE.get("apps_status"):
            return _Resp({"error": "boom"}, STATE["apps_status"])
        pub, owner = STATE.get("publisher", "dom-owner"), STATE.get("owner", "dom-owner")
        mine, other = _item(DOMINO_ID, "domino-deployed-agent", pub, owner), _item(OTHER_ID, "other-project-agent", "someone", "someone")
        pid = params.get("projectId")
        items = [mine, other] if pid is None else ([mine] if pid == STATE["project"] else [other])
        return _Resp({"items": items})
    return _Resp({"items": []})


@pytest.fixture
def env(db_env, monkeypatch, tmp_path):
    STATE.clear()
    STATE["project"] = "proj-123"
    monkeypatch.setenv("DOMINO_API_HOST", "http://domino.invalid")
    monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-123")
    for k in ("ACT_DOMINO_PROJECT_ID", "ACT_DOMINO_SCOPE", "ACT_SERVICE_ACCOUNTS", "DOMINO_STARTING_USERNAME"):
        monkeypatch.delenv(k, raising=False)
    for p in ("AWS", "GOOGLE", "MICROSOFT", "DATABRICKS"):
        monkeypatch.delenv(f"ACT_LIVE_{p}", raising=False)
    import requests
    monkeypatch.setattr(requests, "get", _fake_http)
    monkeypatch.setattr(requests, "request", lambda m, u, *a, **k: _fake_http(u, **k))
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: _fake_http(u, **k))
    monkeypatch.setattr(requests.Session, "get", lambda self, u, *a, **k: _fake_http(u, **k))
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
    assert hasattr(base, "SourceAgent") and hasattr(base, "SourceRun")
    for mod in ("aws", "google", "microsoft", "databricks", "domino"):
        m = importlib.import_module(f"adapters.{mod}")
        cls = [v for k, v in vars(m).items() if k.endswith("Adapter") and isinstance(v, type) and k != "AdapterBase"][0]
        for n in ("list_agents", "get_agent", "list_runs"):
            assert callable(getattr(cls, n, None)), (mod, n)


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
    _intake()
    after = client.get(f"/api/agents/{a['id']}").json()
    assert after["manifest"] != before["manifest"]
    assert after["tier"] < before["tier"]
    ev = [r._mapping for r in _rows(db.audit_events) if r._mapping["action"] == "retier" and r._mapping["target"] == a["id"]]
    assert ev, "expected a retier audit event"
    mrows = [r._mapping for r in _rows(db.manifests) if r._mapping["agent_id"] == a["id"]]
    assert len(mrows) >= 1
    assert any(r["content"] == after["manifest"] for r in mrows)
    assert any("transfer_funds" in json.dumps(r["content"]) for r in mrows)


# ---------------------------------------------------------------- round 2 contracts

def _domino_agents():
    return importlib.import_module("adapters.domino").DominoAdapter().list_agents()


def _apps_calls():
    return STATE.get("calls", [])


def test_ac1_domino_default_scope_is_current_project(env):
    ags = _domino_agents()
    assert [a.native_id for a in ags] == [DOMINO_ID]
    assert _apps_calls() and all(c.get("projectId") == "proj-123" for c in _apps_calls())


def test_ac1_domino_act_project_id_overrides(env, monkeypatch):
    monkeypatch.setenv("ACT_DOMINO_PROJECT_ID", "proj-other")
    ags = _domino_agents()
    assert [a.native_id for a in ags] == [OTHER_ID]
    assert all(c.get("projectId") == "proj-other" for c in _apps_calls())


def test_ac1_domino_scope_all_sends_no_project_filter(env, monkeypatch):
    monkeypatch.setenv("ACT_DOMINO_SCOPE", "all")
    ags = _domino_agents()
    assert {a.native_id for a in ags} == {DOMINO_ID, OTHER_ID}
    assert _apps_calls() and all("projectId" not in c for c in _apps_calls())


def test_ac1_domino_identity_distinct_for_service_account(env, monkeypatch):
    monkeypatch.setenv("ACT_SERVICE_ACCOUNTS", "svc-act-recon, other-svc")
    STATE.update(publisher="svc-act-recon", owner="svc-act-recon")
    ident = _domino_agents()[0].identity
    assert ident["distinct"] is True
    assert ident["type"] == "domino_service_account"
    assert ident["principal"] == "svc-act-recon"


def test_ac1_domino_identity_not_distinct_for_human_publisher(env, monkeypatch):
    monkeypatch.setenv("ACT_SERVICE_ACCOUNTS", "svc-act-recon")
    STATE.update(publisher="alice", owner="alice")
    ident = _domino_agents()[0].identity
    assert ident["distinct"] is False
    assert ident["principal"] == "alice"
    assert ident["type"] != "domino_service_account"


def test_ac1_domino_service_account_flows_to_record(client, monkeypatch):
    monkeypatch.setenv("ACT_SERVICE_ACCOUNTS", "svc-act-recon")
    STATE.update(publisher="svc-act-recon", owner="svc-act-recon")
    _intake()
    a = _by_name(client, "domino-deployed-agent")
    rec = client.get(f"/api/agents/{a['id']}").json()
    assert rec["identity"]["distinct"] is True
    assert rec["identity"]["type"] == "domino_service_account"
    assert rec["identity"]["principal"] == "svc-act-recon"


# -- POST /api/agents/intake takes raw source agents and normalizes server-side

def _raw_aws(name):
    import dataclasses
    mod = importlib.import_module("adapters.aws")
    cls = [v for k, v in vars(mod).items() if k.endswith("Adapter") and isinstance(v, type)][0]
    sa = next(x for x in cls().list_agents() if x.name == name)
    return json.loads(json.dumps(dataclasses.asdict(sa), default=str))


def _post_raw(client, raws, headers=HDR):
    # body key is not fixed by the contract text: send the same list under both plausible keys
    return client.post("/api/agents/intake", json={"agents": raws, "records": raws}, headers=headers)


def test_ac1_intake_route_recomputes_id_and_tier_server_side(client):
    raw = _raw_aws("treasury-sweep-agent")
    raw["tier"] = 4
    raw["id"] = "act:aws:forged"
    r = _post_raw(client, [raw])
    assert r.status_code == 200, r.text
    expected = "act:aws:" + hashlib.sha256(raw["native_id"].encode()).hexdigest()[:16]
    ids = {a["id"] for a in _fleet(client)}
    assert expected in ids and "act:aws:forged" not in ids
    rec = client.get(f"/api/agents/{expected}").json()
    assert rec["id"] == expected
    assert rec["tier"] != 4 and rec["tier"] == 1


@pytest.mark.parametrize("drop", ["native_id", "platform"])
def test_ac1_intake_route_missing_source_fields_422(client, drop):
    raw = _raw_aws("treasury-sweep-agent")
    del raw[drop]
    assert _post_raw(client, [raw]).status_code == 422


def test_ac1_intake_route_anonymous_401(client):
    assert _post_raw(client, [_raw_aws("treasury-sweep-agent")], headers={}).status_code == 401


# -- audit actor and retier on every manifest change

def _audit_after(since_id):
    from backend import db
    return [r._mapping for r in _rows(db.audit_events) if r._mapping["id"] > since_id]


def _max_audit_id():
    from backend import db
    return max([r._mapping["id"] for r in _rows(db.audit_events)] or [0])


def _actors(ev):
    return {ev["actor_user_id"], ev["actor_username"]}


def _edit_aws(fx, name, fn):
    path = fx / "aws" / "list-agent-runtimes.json"
    d = json.loads(path.read_text())
    for rt in d["agentRuntimes"]:
        if rt["agentRuntimeName"] == name:
            fn(rt["metadata"])
    path.write_text(json.dumps(d))


@pytest.fixture
def fx(env, monkeypatch):
    d = env / "fixtures"
    shutil.copytree(ROOT / "fixtures", d)
    monkeypatch.setenv("ACT_FIXTURES_DIR", str(d))
    return d


def test_ac3_retier_when_tier_unchanged_has_from_to_and_actor(client, fx, monkeypatch):
    from backend import db
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "job-runner")
    _intake()
    a = _by_name(client, "treasury-sweep-agent")
    mark = _max_audit_id()
    _edit_aws(fx, "treasury-sweep-agent", lambda m: m["tools"].append("lookup_note"))
    _intake()
    after = client.get(f"/api/agents/{a['id']}").json()
    assert "lookup_note" in json.dumps(after["manifest"])
    assert after["tier"] == 1
    ev = [e for e in _audit_after(mark) if e["action"] == "retier" and e["target"] == a["id"]]
    assert len(ev) == 1, ev
    e = ev[0]
    assert "job-runner" in _actors(e)
    assert "system:intake" not in _actors(e)
    assert set(("from", "to")) <= set(e["detail"])
    assert str(e["detail"]["from"]) == "1" and str(e["detail"]["to"]) == "1"


def test_ac3_inprocess_audit_events_record_caller_not_system(client, fx, monkeypatch):
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "job-runner")
    _intake()
    a = _by_name(client, "trade-break-triage")
    mark = _max_audit_id()

    def esc(m):
        m["tools"].append("transfer_funds")
        m["value_limits"] = {"transfer_funds": {"amount": 5000000, "currency": "USD"}}
        m["criteria"] = {"autonomy": "autonomous", "consequence": "moves_money",
                         "human_in_loop": False, "customer_impact": "high"}
    _edit_aws(fx, "trade-break-triage", esc)
    _intake()
    evs = [e for e in _audit_after(mark) if e["target"] == a["id"]]
    assert any(e["action"] == "retier" for e in evs)
    for e in evs:
        assert "job-runner" in _actors(e), e
        assert "system:intake" not in _actors(e)
    retier = next(e for e in evs if e["action"] == "retier")
    assert str(retier["detail"]["to"]) != str(retier["detail"]["from"])


def test_ac3_http_intake_route_audit_actor_is_caller(client, fx):
    raw = _raw_aws("treasury-sweep-agent")
    assert _post_raw(client, [raw]).status_code == 200
    mark = _max_audit_id()
    raw2 = json.loads(json.dumps(raw))
    raw2["tools"] = list(raw2["tools"]) + ["lookup_note"]
    assert _post_raw(client, [raw2], headers={"X-Domino-User-Id": "u-http", "X-Domino-User": "http-user"}).status_code == 200
    evs = _audit_after(mark)
    ret = [e for e in evs if e["action"] == "retier"]
    assert len(ret) == 1, evs
    assert set(("from", "to")) <= set(ret[0]["detail"])
    for e in evs:
        assert "u-http" in _actors(e), e
        assert "system:intake" not in _actors(e)


# -- adapter failure is surfaced

def test_ac1_domino_api_500_reported_in_intake_errors(client):
    STATE["apps_status"] = 500
    out = _intake()
    assert out["errors"], out
    assert any("domino" in str(e).lower() for e in out["errors"])


def test_ac1_intake_main_exits_nonzero_when_adapter_fails(client, monkeypatch):
    import runpy
    STATE["apps_status"] = 500
    monkeypatch.setenv("ACT_INTAKE_INPROCESS", "1")
    mod = importlib.import_module("jobs.intake")
    if hasattr(mod, "main"):
        rc = mod.main()
    else:  # no main(): execute the script entry point
        with pytest.raises(SystemExit) as ei:
            runpy.run_path(str(ROOT / "jobs" / "intake.py"), run_name="__main__")
        rc = ei.value.code
    assert rc not in (None, 0)


# ---------------------------------------------------------------- round 3 contracts

def test_ac1_domino_fails_closed_without_project_id(env, monkeypatch):
    monkeypatch.delenv("DOMINO_PROJECT_ID", raising=False)
    monkeypatch.delenv("ACT_DOMINO_PROJECT_ID", raising=False)
    monkeypatch.delenv("ACT_DOMINO_SCOPE", raising=False)
    with pytest.raises(Exception):
        _domino_agents()
    assert _apps_calls() == []


def test_ac1_domino_fails_closed_run_intake_reports_error(client, monkeypatch):
    monkeypatch.delenv("DOMINO_PROJECT_ID", raising=False)
    monkeypatch.delenv("ACT_DOMINO_PROJECT_ID", raising=False)
    out = _intake()
    assert any("domino" in str(e).lower() for e in out["errors"]), out
    assert _apps_calls() == []


def test_ac1_domino_drops_items_with_unconfirmed_project(env, monkeypatch):
    import requests
    mine = _item("dom-confirmed", "confirmed", "p", "o")
    mine["projectId"] = "proj-123"
    unknown = _item("dom-unknown", "unknown-project", "p", "o")
    unknown.pop("projectId", None)
    unknown.pop("project", None)
    base = _fake_http

    def fake(url, *a, **k):
        if str(url).endswith("/api/apps/v1/apps"):
            STATE.setdefault("calls", []).append(dict(k.get("params") or {}))
            return _Resp({"items": [mine, unknown]})
        return base(url, *a, **k)
    monkeypatch.setattr(requests, "get", fake)
    monkeypatch.setattr(requests, "request", lambda m, u, *a, **k: fake(u, **k))
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: fake(u, **k))
    monkeypatch.setattr(requests.Session, "get", lambda self, u, *a, **k: fake(u, **k))
    ids = [a.native_id for a in _domino_agents()]
    assert ids == ["dom-confirmed"], ids


@pytest.mark.parametrize("key,bad", [("models", ["not-a-dict"]), ("mcp_servers", [42])])
def test_ac1_intake_route_malformed_dependencies_422(client, key, bad):
    from backend import db
    raw = _raw_aws("treasury-sweep-agent")
    raw["dependencies"] = {key: bad}
    before = len(_rows(db.agents))
    r = _post_raw(client, [raw])
    assert r.status_code == 422, r.text
    assert len(_rows(db.agents)) == before


def test_ac1_inprocess_bad_record_does_not_block_valid_ones(client, monkeypatch):
    import dataclasses
    from backend import db
    aws = importlib.import_module("adapters.aws")
    good = aws.AwsAdapter().list_agents()[0]
    bad = dataclasses.replace(aws.AwsAdapter().list_agents()[1], dependencies={"models": ["x"]})
    monkeypatch.setattr(aws.AwsAdapter, "list_agents", lambda self: [good, bad])
    monkeypatch.setenv("ACT_INTAKE_INPROCESS", "1")
    out = _intake()  # must not raise
    assert out["errors"], out
    names = {r._mapping["name"] for r in _rows(db.agents)}
    assert good.name in names
    assert bad.name not in names
