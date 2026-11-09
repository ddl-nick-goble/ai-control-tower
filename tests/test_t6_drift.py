"""T-6 tests: drift detector and findings (AC-6, AC-12 backend part)."""
import importlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

import test_t4_intake as t4  # reuse the stubbed Domino HTTP + env approach

ROOT = Path(__file__).resolve().parent.parent
HDR = {"X-Domino-User-Id": "u1", "X-Domino-User": "tester"}
FINDING_SCHEMA = json.loads((ROOT / "schemas" / "finding.json").read_text())
DRIFT = json.loads((ROOT / "fixtures" / "runs" / "weekend-drift.json").read_text())
NORMAL = json.loads((ROOT / "fixtures" / "runs" / "otel-genai-treasury-sweep-normal.json").read_text())
STUB_TEXT = "The agent called a tool outside its approved manifest."
T0 = 1_760_000_000_000_000_000
NATIVE = "native-t6"


def _kv(k, v):
    if isinstance(v, (dict, list)):
        v = json.dumps(v)
    return {"key": k, "value": {"stringValue": str(v)}}


def _span(i, tool, args, trace):
    return {"traceId": trace, "spanId": f"{i + 1:016x}", "name": f"execute_tool {tool}", "kind": 1,
            "startTimeUnixNano": str(T0 + i * 1_000_000_000), "endTimeUnixNano": str(T0 + i * 1_000_000_000 + 5),
            "attributes": [_kv("gen_ai.operation.name", "execute_tool"), _kv("gen_ai.tool.name", tool),
                           _kv("gen_ai.tool.call.arguments", args), _kv("gen_ai.agent.id", NATIVE)]}


def _otlp(spans):
    return {"resourceSpans": [{"resource": {"attributes": [_kv("service.name", "svc")]},
                               "scopeSpans": [{"scope": {"name": "t"}, "spans": spans}]}]}


@pytest.fixture
def llm_calls(monkeypatch):
    return []


@pytest.fixture
def client(db_env, monkeypatch, llm_calls):
    monkeypatch.setenv("DOMINO_API_HOST", "http://domino.invalid")
    monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-123")
    t4.STATE.clear()
    t4.STATE["project"] = "proj-123"
    import requests
    monkeypatch.setattr(requests, "get", t4._fake_http)
    monkeypatch.setattr(requests, "request", lambda m, u, *a, **k: t4._fake_http(u, **k))
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: t4._fake_http(u, **k))
    monkeypatch.setattr(requests.Session, "get", lambda self, u, *a, **k: t4._fake_http(u, **k))
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    llm = importlib.import_module("backend.llm")  # feature module; missing until built

    def fake(alias, *a, **k):
        llm_calls.append(alias)
        return {"content": [{"type": "text", "text": STUB_TEXT}], "model": "stub"}
    monkeypatch.setattr(llm, "create_message", fake)
    with TestClient(main.app) as c:
        yield c


def _seed(db_env, tier, tools=("A", "B"), limits=None, aid="act:test:t6"):
    manifest = {"tools": list(tools), "data_scopes": [], "systems": [],
                "value_limits": limits or {}, "value_limits_detail": limits or {}, "permissions": {}}
    con = sqlite3.connect(str(db_env))
    con.execute("insert into agents (id, source_platform, native_id, name, status, tier, record) values (?,?,?,?,?,?,?)",
                (aid, "test", NATIVE, "T6 Agent", "discovered", str(tier), json.dumps({"id": aid, "manifest": manifest})))
    con.execute("insert into manifests (agent_id, version, content, created_at) values (?,?,?,?)",
                (aid, "1", json.dumps(manifest), "2026-11-01T00:00:00Z"))
    con.commit()
    con.close()
    return aid


def _post(client, spans):
    return client.post("/v1/traces", json=_otlp(spans), headers=HDR)


def _findings(client, aid):
    r = client.get(f"/api/agents/{aid}/findings")
    assert r.status_code == 200, r.text
    return r.json()


def _weekend(client):
    t4.importlib.import_module("jobs.intake").run_intake()
    a = next(x for x in client.get("/api/agents").json() if x["name"] == "treasury-sweep-agent")
    return a["id"]


@pytest.mark.parametrize("tier,sev", [(1, "critical"), (2, "high"), (3, "medium"), (4, "low")])
def test_ac6_scope_drift_severity_from_tier(client, db_env, tier, sev):
    aid = _seed(db_env, tier)
    trace = "b" * 32
    assert _post(client, [_span(0, "A", {}, trace), _span(1, "C", {}, trace)]).status_code == 200
    fs = _findings(client, aid)
    assert len(fs) == 1
    f = fs[0]
    assert f["type"] == "scope_drift" and f["severity"] == sev
    assert f["tool"] == "C" and f["span_id"] == f"{2:016x}" and f["run_id"] == trace
    assert f["status"] == "open"
    assert f["ts"].startswith("2025-10-09T08:53:21")  # T0 + 1s
    assert f["reason"] == "tool_not_in_manifest"
    jsonschema.validate(f, FINDING_SCHEMA, format_checker=jsonschema.FormatChecker())


def test_ac6_finding_appears_within_5_seconds(client, db_env):
    aid = _seed(db_env, 2)
    t = time.monotonic()
    _post(client, [_span(0, "C", {}, "c" * 32)])
    while not _findings(client, aid) and time.monotonic() - t < 5:
        time.sleep(0.05)
    assert time.monotonic() - t < 5
    assert len(_findings(client, aid)) == 1


def test_ac6_in_manifest_run_creates_no_finding(client, db_env):
    aid = _seed(db_env, 1)
    _post(client, [_span(0, "A", {}, "d" * 32), _span(1, "B", {}, "d" * 32)])
    assert _findings(client, aid) == []
    assert next(a for a in client.get("/api/agents").json() if a["id"] == aid)["open_findings"] == 0


def test_ac6_normal_fixture_creates_no_finding(client):
    aid = _weekend(client)
    assert client.post("/v1/traces", json=NORMAL, headers=HDR).status_code == 200
    assert _findings(client, aid) == []


def test_ac6_idempotent_across_repost_and_hook_calls(client, db_env):
    aid = _seed(db_env, 1)
    spans = [_span(0, "C", {}, "e" * 32), _span(1, "D", {}, "e" * 32)]
    _post(client, spans)
    _post(client, spans)
    _post(client, spans + [_span(2, "A", {}, "e" * 32)])
    traces = importlib.import_module("backend.routes.traces")
    for _ in range(2):
        for fn in list(traces.run_ingested_hooks):
            fn("e" * 32)
    fs = _findings(client, aid)
    assert len(fs) == 1
    assert fs[0]["tool"] == "C"
    assert {v["tool"] for v in fs[0]["detail"]["violations"]} == {"C", "D"}


def test_ac6_open_findings_count_and_get_by_id(client, db_env):
    aid = _seed(db_env, 1)
    _post(client, [_span(0, "C", {}, "f" * 32)])
    assert next(a for a in client.get("/api/agents").json() if a["id"] == aid)["open_findings"] == 1
    f = _findings(client, aid)[0]
    r = client.get(f"/api/findings/{f['id']}")
    assert r.status_code == 200
    j = r.json()
    assert j["run_id"] == "f" * 32 and j["timeline_url"] == f"runs/{'f' * 32}/timeline"
    assert client.get("/api/findings/999999").status_code == 404


def test_ac6_value_limit_read_from_tool_arguments(client, db_env):
    aid = _seed(db_env, 1, tools=("transfer_funds",), limits={"transfer_funds": {"amount": 1000, "currency": "USD"}})
    _post(client, [_span(0, "transfer_funds", {"amount": 500}, "1" * 32)])
    assert _findings(client, aid) == []
    _post(client, [_span(0, "transfer_funds", {"amount": 5000}, "2" * 32)])
    f = _findings(client, aid)[0]
    assert f["reason"] == "value_limit_exceeded" and f["observed_value"] == 5000 and f["limit"] == 1000
    jsonschema.validate(f, FINDING_SCHEMA, format_checker=jsonschema.FormatChecker())


def test_ac6_explanation_from_haiku_stub(client, db_env, llm_calls):
    aid = _seed(db_env, 3)
    _post(client, [_span(0, "C", {}, "3" * 32)])
    assert _findings(client, aid)[0]["explanation"] == STUB_TEXT
    assert llm_calls and set(llm_calls) == {"haiku"}


def test_ac6_llm_failure_still_creates_finding_with_fallback(client, db_env, monkeypatch):
    llm = importlib.import_module("backend.llm")

    def boom(*a, **k):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(llm, "create_message", boom)
    aid = _seed(db_env, 1)
    assert _post(client, [_span(0, "C", {}, "4" * 32)]).status_code == 200
    f = _findings(client, aid)[0]
    assert isinstance(f["explanation"], str) and f["explanation"].strip()
    assert f["explanation"] != STUB_TEXT


def test_ac6_slow_llm_does_not_block_ingest(client, db_env, monkeypatch):
    llm = importlib.import_module("backend.llm")

    def slow(*a, **k):
        time.sleep(8)
        return {"content": [{"type": "text", "text": "late"}], "model": "stub"}
    monkeypatch.setattr(llm, "create_message", slow)
    aid = _seed(db_env, 1)
    t = time.monotonic()
    assert _post(client, [_span(0, "C", {}, "5" * 32)]).status_code == 200
    assert time.monotonic() - t < 5
    deadline = time.monotonic() + 5
    while not _findings(client, aid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(_findings(client, aid)) == 1


def test_ac12_weekend_drift_single_open_finding_with_timeline(client):
    aid = _weekend(client)
    t = time.monotonic()
    assert client.post("/v1/traces", json=DRIFT, headers=HDR).status_code == 200
    assert client.post("/v1/traces", json=DRIFT, headers=HDR).status_code == 200  # re-post
    assert time.monotonic() - t < 5 + 5
    fs = [f for f in _findings(client, aid) if f["type"] == "scope_drift"]
    assert len(fs) == 1
    f = fs[0]
    assert f["status"] == "open" and f["severity"] == "critical"
    assert f["tool"] == "transfer_funds" and f["reason"] == "value_limit_exceeded"
    assert f["observed_value"] == 4750000 and f["limit"] == 250000
    assert f["ts"].startswith("2026-11-07T02:14:00")
    assert f["span_id"] == "8df4f50947aaeb26"
    viol = {v["tool"]: v for v in f["detail"]["violations"]}
    assert viol["list_credentials"]["reason"] == "tool_not_in_manifest"
    assert viol["transfer_funds"]["reason"] == "value_limit_exceeded"
    jsonschema.validate(f, FINDING_SCHEMA, format_checker=jsonschema.FormatChecker())
    g = client.get(f"/api/findings/{f['id']}").json()
    assert g["run_id"] == f["run_id"] and g["timeline_url"] == f"runs/{f['run_id']}/timeline"
    a = next(x for x in client.get("/api/agents").json() if x["id"] == aid)
    assert a["open_findings"] == 1
