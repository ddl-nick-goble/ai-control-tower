import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
HDR = {"X-Domino-User-Id": "u1", "X-Domino-User": "tester"}
ACT_ID = "act:test:0123456789abcdef"
NATIVE = "native-agent-1"
T0 = 1_760_000_000_000_000_000


def _kv(k, v):
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, (dict, list)):
        v = json.dumps(v)
    return {"key": k, "value": {"stringValue": str(v)}}


def span(name, attrs, i=0, trace="a" * 32, parent=None):
    s = {
        "traceId": trace,
        "spanId": f"{i + 1:016x}",
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(T0 + i * 1_000_000),
        "endTimeUnixNano": str(T0 + i * 1_000_000 + 500_000),
        "attributes": [_kv(k, v) for k, v in attrs.items()],
    }
    if parent:
        s["parentSpanId"] = parent
    return s


def otlp(spans):
    return {"resourceSpans": [{
        "resource": {"attributes": [_kv("service.name", "svc")]},
        "scopeSpans": [{"scope": {"name": "t"}, "spans": spans}],
    }]}


@pytest.fixture
def client(db_env, db_mod):
    import importlib
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def db_path(db_env):
    return str(db_env)


def seed_agent(db_path, native=NATIVE):
    con = sqlite3.connect(db_path)
    con.execute(
        "insert into agents (id, source_platform, native_id, name, status) values (?,?,?,?,?)",
        (ACT_ID, "test", native, "Test Agent", "discovered"),
    )
    con.commit()
    con.close()


def post(client, spans, headers=HDR):
    return client.post("/v1/traces", json=otlp(spans), headers=headers)


def rows(db_path, sql, args=()):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def canon(row):
    ev = row["event"]
    return json.loads(ev) if isinstance(ev, str) else ev


def validate(ev):
    import jsonschema
    schema = json.loads((ROOT / "schemas" / "canonical-event.json").read_text())
    jsonschema.validate(ev, schema, format_checker=jsonschema.FormatChecker())


def ingest_one(client, db_path, name, attrs):
    seed_agent(db_path)
    r = post(client, [span(name, attrs)])
    assert r.status_code == 200, r.text
    evs = rows(db_path, "select * from events")
    assert len(evs) == 1
    return r, evs[0]


# ---------------- AC-4 ----------------

def test_ac4_anonymous_post_returns_401(client):
    r = client.post("/v1/traces", json=otlp([span("x", {})]))
    assert r.status_code == 401


def test_ac4_otel_genai_tool_span_canonical(client, db_path):
    r, row = ingest_one(client, db_path, "execute_tool lookup", {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "lookup",
        "gen_ai.agent.id": NATIVE,
    })
    assert r.json() == {"accepted": 1, "agents_matched": 1}
    ev = canon(row)
    validate(ev)
    assert ev["kind"] == "tool_call"
    assert ev["name"] == "lookup"
    assert ev["agent_id"] == ACT_ID
    assert row["agent_id"] == ACT_ID
    assert row["raw_span"]


def test_ac4_otel_genai_chat_is_model_call(client, db_path):
    _, row = ingest_one(client, db_path, "chat gpt", {
        "gen_ai.operation.name": "chat", "gen_ai.agent.id": NATIVE})
    ev = canon(row)
    validate(ev)
    assert ev["kind"] == "model_call"


def test_ac4_openinference_tool_span_canonical(client, db_path):
    _, row = ingest_one(client, db_path, "my_tool", {
        "openinference.span.kind": "TOOL",
        "tool.name": "search_db",
        "gen_ai.agent.id": NATIVE,
    })
    ev = canon(row)
    validate(ev)
    assert ev["kind"] == "tool_call"
    assert ev["name"] == "search_db"
    assert ev["agent_id"] == ACT_ID


def test_ac4_openinference_llm_is_model_call(client, db_path):
    _, row = ingest_one(client, db_path, "llm", {
        "openinference.span.kind": "LLM", "gen_ai.agent.id": NATIVE})
    ev = canon(row)
    validate(ev)
    assert ev["kind"] == "model_call"


def test_ac4_mlflow_tool_span_canonical(client, db_path):
    _, row = ingest_one(client, db_path, "mlflow_tool", {
        "mlflow.spanType": "TOOL",
        "gen_ai.agent.id": NATIVE,
    })
    ev = canon(row)
    validate(ev)
    assert ev["kind"] == "tool_call"
    assert "mlflow_tool" in ev["name"]
    assert ev["agent_id"] == ACT_ID


def test_ac4_mlflow_chat_model_is_model_call(client, db_path):
    _, row = ingest_one(client, db_path, "chat", {
        "mlflow.spanType": "CHAT_MODEL", "gen_ai.agent.id": NATIVE})
    assert canon(row)["kind"] == "model_call"


def test_ac4_protobuf_ingest(client, db_path):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span
    from opentelemetry.proto.common.v1.common_pb2 import KeyValue, AnyValue
    seed_agent(db_path)
    sp = Span(trace_id=bytes.fromhex("b" * 32), span_id=bytes.fromhex("0000000000000001"),
              name="execute_tool pb", start_time_unix_nano=T0, end_time_unix_nano=T0 + 1000)
    for k, v in {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "pbtool",
                 "gen_ai.agent.id": NATIVE}.items():
        sp.attributes.append(KeyValue(key=k, value=AnyValue(string_value=v)))
    req = ExportTraceServiceRequest(resource_spans=[ResourceSpans(scope_spans=[ScopeSpans(spans=[sp])])])
    r = client.post("/v1/traces", content=req.SerializeToString(),
                    headers={**HDR, "Content-Type": "application/x-protobuf"})
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": 1, "agents_matched": 1}
    ev = canon(rows(db_path, "select * from events")[0])
    validate(ev)
    assert ev["kind"] == "tool_call" and ev["name"] == "pbtool" and ev["agent_id"] == ACT_ID


def test_ac4_unmatched_agent_creates_shadow_finding(client, db_path):
    r = post(client, [span("execute_tool x", {
        "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "x",
        "gen_ai.agent.id": "unknown-agent"})])
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": 1, "agents_matched": 0}
    assert len(rows(db_path, "select * from events")) == 1  # still stored
    fs = rows(db_path, "select * from findings")
    assert any("shadow_agent" in json.dumps(f, default=str) for f in fs), fs


def test_ac4_hash_chain_verifies(client, db_path):
    seed_agent(db_path)
    spans = [span(f"execute_tool t{i}", {"gen_ai.operation.name": "execute_tool",
             "gen_ai.tool.name": f"t{i}", "gen_ai.agent.id": NATIVE}, i) for i in range(5)]
    assert post(client, spans).status_code == 200
    n = len(rows(db_path, "select * from events"))
    assert n == 5
    r = client.get("/api/audit/verify")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "events": n}


def test_ac4_hash_chain_links_sha256(client, db_path):
    import hashlib
    seed_agent(db_path)
    spans = [span(f"execute_tool t{i}", {"gen_ai.operation.name": "execute_tool",
             "gen_ai.tool.name": f"t{i}", "gen_ai.agent.id": NATIVE}, i) for i in range(3)]
    post(client, spans)
    evs = rows(db_path, "select * from events order by id")
    for prev, cur in zip(evs, evs[1:]):
        assert cur["prev_hash"] == prev["hash"]
    assert all(len(e["hash"]) == 64 for e in evs)
    assert len({e["hash"] for e in evs}) == 3
    # hash depends on prev_hash: sha256(prev_hash || canonical_json) over some serialization
    cur = evs[1]
    ev = cur["event"] if isinstance(cur["event"], str) else json.dumps(cur["event"])
    cands = {json.dumps(json.loads(ev), sort_keys=True, separators=(",", ":")),
             json.dumps(json.loads(ev), sort_keys=True), json.dumps(json.loads(ev)),
             json.dumps(json.loads(ev), separators=(",", ":")), ev}
    assert any(hashlib.sha256((cur["prev_hash"] + c).encode()).hexdigest() == cur["hash"]
               for c in cands)


def test_ac4_tamper_detected(client, db_path):
    seed_agent(db_path)
    spans = [span(f"execute_tool t{i}", {"gen_ai.operation.name": "execute_tool",
             "gen_ai.tool.name": f"t{i}", "gen_ai.agent.id": NATIVE}, i) for i in range(4)]
    post(client, spans)
    assert client.get("/api/audit/verify").json()["ok"] is True
    evs = rows(db_path, "select * from events order by id")
    ev = canon(evs[1])
    ev["name"] = "tampered"
    con = sqlite3.connect(db_path)
    con.execute("update events set event=? where id=?", (json.dumps(ev), evs[1]["id"]))
    con.commit()
    con.close()
    assert client.get("/api/audit/verify").json()["ok"] is False


# ---------------- AC-5 ----------------

def test_ac5_timeline_ordered_and_redacted(client, db_path):
    seed_agent(db_path)
    trace = "c" * 32
    spans = [
        span("execute_tool pay", {
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "pay",
            "gen_ai.agent.id": NATIVE,
            "gen_ai.tool.call.arguments": {"amount": 4200, "password": "hunter2-secret-value",
                                           "ssn": "123-45-6789"}}, 2, trace),
        span("chat m", {"gen_ai.operation.name": "chat", "gen_ai.agent.id": NATIVE}, 1, trace),
        span("invoke_agent a", {"gen_ai.operation.name": "invoke_agent",
                                "gen_ai.agent.id": NATIVE}, 0, trace),
    ]
    assert post(client, spans).status_code == 200
    r = client.get(f"/api/runs/{trace}/timeline")
    assert r.status_code == 200, r.text
    body = r.text
    assert "hunter2-secret-value" not in body
    assert "123-45-6789" not in body
    assert "4200" in body
    data = r.json()
    items = data if isinstance(data, list) else (data.get("events") or data.get("timeline"))
    assert len(items) == 3
    ts = [e["ts"] for e in items]
    assert ts == sorted(ts)
    assert [e["kind"] for e in items if e["kind"] == "tool_call"] == ["tool_call"]


def test_ac5_timeline_500_spans_under_2s(client, db_path):
    seed_agent(db_path)
    trace = "d" * 32
    spans = [span(f"execute_tool t{i}", {
        "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": f"t{i}",
        "gen_ai.agent.id": NATIVE,
        "gen_ai.tool.call.arguments": {"amount": i, "token": f"tok-{i}"}}, i, trace)
        for i in range(500)]
    r = post(client, spans)
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 500
    t = time.perf_counter()
    r = client.get(f"/api/runs/{trace}/timeline")
    dt = time.perf_counter() - t
    assert r.status_code == 200
    data = r.json()
    items = data if isinstance(data, list) else (data.get("events") or data.get("timeline"))
    assert len(items) == 500
    assert dt < 2.0
