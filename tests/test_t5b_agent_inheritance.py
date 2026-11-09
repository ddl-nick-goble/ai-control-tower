"""T-5b: child spans inherit the run's agent (AC-4)."""
import json
import sqlite3
from pathlib import Path

import pytest

from test_t4_intake import env, client, _intake, HDR  # noqa: F401  (stubbed intake env + app client)

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "fixtures" / "runs"
FIXTURES = ["weekend-drift", "otel-genai-treasury-sweep-normal", "otel-genai-kyc-refresh",
            "openinference-credit-memo", "mlflow-liquidity-forecast"]


def load(name):
    return json.loads((RUNS / f"{name}.json").read_text())


def all_spans(payload):
    return [s for rs in payload["resourceSpans"] for ss in rs["scopeSpans"] for s in ss["spans"]]


def attr(s, key):
    for a in s.get("attributes", []):
        if a["key"] == key:
            return next(iter(a["value"].values()))
    return None


def native_of(payload):
    ids = {attr(s, "gen_ai.agent.id") for s in all_spans(payload)} - {None}
    assert len(ids) == 1
    return ids.pop()


def subset(payload, spans):
    return {"resourceSpans": [{"resource": rs.get("resource", {}),
                               "scopeSpans": [{"scope": ss.get("scope", {}), "spans": spans}]}
                              for rs in payload["resourceSpans"][:1] for ss in rs["scopeSpans"][:1]]}


def q(client, sql, args=()):
    from backend import db
    con = sqlite3.connect(str(db.engine.url.database))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def agent_for(client, native):
    rows = q(client, "select id from agents where native_id = ?", (native,))
    assert len(rows) == 1, f"no seeded agent for {native}"
    return rows[0]["id"]


def ev(client, payload, cols="agent_id, event"):
    """Events stored for the spans of this payload (intake may seed other events)."""
    ids = {x["spanId"] for x in all_spans(payload)}
    rows = q(client, f"select {cols}, span_id from events")
    return [r for r in rows if r["span_id"] in ids]


def post(client, payload):
    r = client.post("/v1/traces", json=payload, headers=HDR)
    assert r.status_code == 200, r.text
    return r


def trace_of(payload):
    return all_spans(payload)[0]["traceId"]


def run_events(client, payload):
    # run_id derivation is the app's; select by span ids stored raw for this trace
    rows = q(client, "select run_id, agent_id, event from events")
    tid = trace_of(payload)
    return [r for r in rows if tid in (r["run_id"] or "") or tid in r["event"]]


def assert_verify(client):
    r = client.get("/api/audit/verify")
    assert r.status_code == 200
    assert r.json()["ok"] is True, r.json()


def test_ac4_weekend_drift_all_40_spans_link_to_treasury(client):
    _intake()
    p = load("weekend-drift")
    act = agent_for(client, native_of(p))
    post(client, p)
    evs = ev(client, p)
    assert len(evs) == 40
    assert {e["agent_id"] for e in evs} == {act}, {e["agent_id"] for e in evs}
    assert {json.loads(e["event"])["agent_id"] for e in evs} == {act}
    assert_verify(client)


@pytest.mark.parametrize("name", FIXTURES)
def test_ac4_every_fixture_run_events_carry_matched_agent(client, name):
    _intake()
    p = load(name)
    act = agent_for(client, native_of(p))
    post(client, p)
    evs = ev(client, p)
    assert len(evs) == len(all_spans(p))
    assert {e["agent_id"] for e in evs} == {act}
    assert {json.loads(e["event"])["agent_id"] for e in evs} == {act}
    assert_verify(client)


@pytest.mark.parametrize("name", ["weekend-drift", "otel-genai-kyc-refresh", "mlflow-liquidity-forecast"])
def test_ac4_split_batches_children_first_then_agent_span(client, name):
    _intake()
    p = load(name)
    spans = all_spans(p)
    carriers = [s for s in spans if attr(s, "gen_ai.agent.id")]
    children = [s for s in spans if not attr(s, "gen_ai.agent.id")]
    assert carriers and children
    act = agent_for(client, native_of(p))
    post(client, subset(p, children))
    first = {e["span_id"]: (e["event"], e["hash"], e["prev_hash"])
             for e in ev(client, p, "agent_id, event, hash, prev_hash")}
    assert len(first) == len(children)
    post(client, subset(p, carriers))
    evs = ev(client, p, "agent_id, event, hash, prev_hash, run_id")
    assert len(evs) == len(spans)
    # the link column is re-pointed; append-only canonical events and hashes are untouched
    assert {e["agent_id"] for e in evs} == {act}, {e["agent_id"] for e in evs}
    for e in evs:
        if e["span_id"] in first:
            assert (e["event"], e["hash"], e["prev_hash"]) == first[e["span_id"]]
    assert_verify(client)
    for run_id in {e["run_id"] for e in evs}:
        r = client.get(f"/api/runs/{run_id}/timeline")
        assert r.status_code == 200
        assert r.json()["agent_id"] == act


def test_ac4_unmatched_agent_run_links_all_spans_to_one_shadow_placeholder(client):
    p = load("weekend-drift")
    native = native_of(p)
    # no intake: agent is unmatched
    post(client, p)
    agents = q(client, "select id, status from agents where native_id = ?", (native,))
    assert len(agents) == 1 and agents[0]["status"] == "shadow"
    evs = ev(client, p)
    assert len(evs) == 40
    assert {e["agent_id"] for e in evs} == {agents[0]["id"]}, {e["agent_id"] for e in evs}
    assert {json.loads(e["event"])["agent_id"] for e in evs} == {agents[0]["id"]}
    assert len(q(client, "select id from agents where status = 'shadow'")) == 1
    assert_verify(client)


def test_ac4_split_batch_unmatched_agent_relinks_to_shadow(client):
    p = load("weekend-drift")
    spans = all_spans(p)
    post(client, subset(p, [s for s in spans if not attr(s, "gen_ai.agent.id")]))
    first = {e["span_id"]: (e["event"], e["hash"], e["prev_hash"])
             for e in ev(client, p, "agent_id, event, hash, prev_hash")}
    assert first
    post(client, subset(p, [s for s in spans if attr(s, "gen_ai.agent.id")]))
    shadow = q(client, "select id from agents where status = 'shadow'")
    assert len(shadow) == 1
    evs = ev(client, p, "agent_id, event, hash, prev_hash")
    assert len(evs) == 40
    assert {e["agent_id"] for e in evs} == {shadow[0]["id"]}, {e["agent_id"] for e in evs}
    for e in evs:
        if e["span_id"] in first:
            assert (e["event"], e["hash"], e["prev_hash"]) == first[e["span_id"]]
    assert_verify(client)


def test_ac4_per_agent_event_counts_reflect_all_spans(client):
    _intake()
    drift, normal = load("weekend-drift"), load("otel-genai-treasury-sweep-normal")
    act = agent_for(client, native_of(drift))
    assert native_of(normal) == native_of(drift)
    base = q(client, "select count(*) c from events where agent_id = ?", (act,))[0]["c"]
    post(client, drift)
    post(client, normal)
    total = len(all_spans(drift)) + len(all_spans(normal))
    before = base
    assert q(client, "select count(*) c from events where agent_id = ?", (act,))[0]["c"] == before + total
    assert len(ev(client, drift)) + len(ev(client, normal)) == total
    assert all(e["agent_id"] == act for e in ev(client, drift) + ev(client, normal))
    r = client.get(f"/api/agents/{act}/runs")
    if r.status_code == 200:
        body = r.json()
        runs = body if isinstance(body, list) else body.get("runs", body.get("items", []))
        assert len(runs) >= 2
    assert_verify(client)
