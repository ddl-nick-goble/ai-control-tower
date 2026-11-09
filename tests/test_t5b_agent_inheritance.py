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


def fresh(payload):
    """Rewrite a fixture run into a genuinely new run (new traceId/spanIds, parents kept consistent)."""
    import copy
    import secrets
    p = copy.deepcopy(payload)
    spans = all_spans(p)
    tmap, smap = {}, {}
    for s in spans:
        tmap.setdefault(s["traceId"], secrets.token_hex(16))
        smap[s["spanId"]] = secrets.token_hex(8)
    for s in spans:
        s["traceId"] = tmap[s["traceId"]]
        s["spanId"] = smap[s["spanId"]]
        if s.get("parentSpanId"):
            s["parentSpanId"] = smap.get(s["parentSpanId"], s["parentSpanId"])
    return p


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
    p = fresh(load(name))
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
    drift, normal = fresh(load("weekend-drift")), fresh(load("otel-genai-treasury-sweep-normal"))
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


# ---- round 2: multi-agent traces, ancestor inheritance, run agent, conflicts, replay ----
import secrets

NA, NB, NC = ("treasury_sweep_agent-Xk3Lq9ZpT2", "kyc_refresh_agent-Rt7Hn4WvB8",
              "b2e4d0a3-5c8f-4f2b-8d66-1e3f7a9b2c11")


def mk(trace, parent=None, native=None, op="execute_tool", t=0, name=None):
    attrs = [{"key": "gen_ai.operation.name", "value": {"stringValue": op}}]
    if native:
        attrs.append({"key": "gen_ai.agent.id", "value": {"stringValue": native}})
    if op == "execute_tool":
        attrs.append({"key": "gen_ai.tool.name", "value": {"stringValue": name or "lookup"}})
    s = {"traceId": trace, "spanId": secrets.token_hex(8), "name": name or op, "kind": 1,
         "startTimeUnixNano": str(1794015539000000000 + t * 1000000000),
         "endTimeUnixNano": str(1794015539000000000 + (t + 1) * 1000000000),
         "attributes": attrs, "status": {"code": 1}}
    if parent:
        s["parentSpanId"] = parent["spanId"]
    return s


def wrap(spans):
    return {"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"scope": {}, "spans": spans}]}]}


def tree(trace):
    """root A -> toolA1, toolA2, childB(B) -> toolB1, toolB2"""
    root = mk(trace, native=NA, op="invoke_agent", t=0)
    ta1, ta2 = mk(trace, root, t=1), mk(trace, root, t=2)
    cb = mk(trace, root, native=NB, op="invoke_agent", t=3)
    tb1, tb2 = mk(trace, cb, t=4), mk(trace, cb, t=5)
    return root, [ta1, ta2], cb, [tb1, tb2]


def link_of(client, span):
    rows = q(client, "select agent_id, run_id from events where span_id = ?", (span["spanId"],))
    assert len(rows) == 1, rows
    return rows[0]["agent_id"]


def run_id_of(client, span):
    return q(client, "select run_id from events where span_id = ?", (span["spanId"],))[0]["run_id"]


@pytest.mark.parametrize("order", ["parents_first", "children_first", "shuffled"])
def test_ac4_ancestor_inheritance_two_agents_in_one_trace(client, order):
    _intake()
    a, b = agent_for(client, NA), agent_for(client, NB)
    root, ta, cb, tb = tree(secrets.token_hex(16))
    spans = [root, *ta, cb, *tb]
    if order == "children_first":
        spans = [*tb, *ta, cb, root]
    elif order == "shuffled":
        spans = [tb[0], ta[1], root, tb[1], cb, ta[0]]
    post(client, wrap(spans))
    for s in [root, *ta]:
        assert link_of(client, s) == a, s["name"]
    for s in [cb, *tb]:
        assert link_of(client, s) == b, s["name"]
    assert_verify(client)


@pytest.mark.parametrize("order", ["parents_first", "children_first", "shuffled"])
def test_ac4_run_agent_is_root_spans_agent(client, order):
    _intake()
    a = agent_for(client, NA)
    root, ta, cb, tb = tree(secrets.token_hex(16))
    spans = {"parents_first": [root, *ta, cb, *tb], "children_first": [*tb, cb, *ta, root],
             "shuffled": [cb, tb[0], root, ta[0], tb[1], ta[1]]}[order]
    post(client, wrap(spans))
    rid = run_id_of(client, root)
    rows = q(client, "select agent_id from runs where id = ?", (rid,))
    assert len(rows) == 1 and rows[0]["agent_id"] == a, rows
    r = client.get(f"/api/runs/{rid}/timeline")
    assert r.status_code == 200
    assert r.json()["agent_id"] == a


def conflicts(client):
    return [r for r in q(client, "select action, detail, target from audit_events")
            if r["action"] == "run_agent_conflict"]


def test_ac4_conflicting_root_agent_is_recorded_and_stored_wins(client):
    _intake()
    a, c = agent_for(client, NA), agent_for(client, NC)
    trace = secrets.token_hex(16)
    root, ta, cb, tb = tree(trace)
    post(client, wrap([root, *ta, cb, *tb]))
    rid = run_id_of(client, root)
    assert q(client, "select agent_id from runs where id = ?", (rid,))[0]["agent_id"] == a
    assert not [x for x in conflicts(client) if rid in json.dumps(x)]
    # later batch: a root-level span claims C; plus an id-less span with no ancestor id, and a span under C
    claim = mk(trace, native=NC, op="invoke_agent", t=10)
    own = mk(trace, claim, t=11)               # inherits C from its ancestor
    orphan = mk(trace, t=12)                   # no id, no parent: falls back to run agent A
    post(client, wrap([claim, own, orphan]))
    assert q(client, "select agent_id from runs where id = ?", (rid,))[0]["agent_id"] == a
    assert run_id_of(client, claim) == rid
    cs = [x for x in conflicts(client) if rid in json.dumps(x)]
    assert cs, "no run_agent_conflict audit event"
    blob = json.dumps(cs[0]["detail"]) + (cs[0]["target"] or "")
    assert rid in blob and a in blob and c in blob, blob
    assert link_of(client, claim) == c
    assert link_of(client, own) == c
    assert link_of(client, orphan) == a
    # earlier spans untouched
    assert link_of(client, root) == a and link_of(client, cb) == agent_for(client, NB)
    assert_verify(client)


def test_ac4_replayed_unmatched_run_yields_single_shadow_finding_on_first_span(client):
    trace = secrets.token_hex(16)
    root = mk(trace, native="ghost-agent-zz9", op="invoke_agent", t=0)
    kids = [mk(trace, root, t=1), mk(trace, root, t=2)]
    p = wrap([root, *kids])
    post(client, p)
    first_ids = {s["spanId"] for s in [root, *kids]}
    post(client, json.loads(json.dumps(p)))
    fs = q(client, "select span_id, run_id from findings where rule = 'shadow_agent'")
    assert len(fs) == 1, fs
    assert fs[0]["span_id"] in first_ids
    stored = {r["span_id"] for r in q(client, "select span_id from events where run_id = ?", (fs[0]["run_id"],))}
    assert fs[0]["span_id"] in stored
    assert len(stored) == 3
    assert_verify(client)


def test_ac4_fallback_uses_runs_row_agent_when_stored_events_have_several_agents(client):
    _intake()
    a = agent_for(client, NA)
    trace = secrets.token_hex(16)
    root, ta, cb, tb = tree(trace)
    post(client, wrap([root, *ta, cb, *tb]))     # stored events carry both A and B
    rid = run_id_of(client, root)
    assert {r["agent_id"] for r in q(client, "select agent_id from events where run_id = ?", (rid,))} \
        == {a, agent_for(client, NB)}
    late = [mk(trace, t=20 + i) for i in range(3)]   # no id, no parent: fallback to stored run agent
    post(client, wrap(late))
    assert q(client, "select agent_id from runs where id = ?", (rid,))[0]["agent_id"] == a
    for s in late:
        assert link_of(client, s) == a
    assert_verify(client)
