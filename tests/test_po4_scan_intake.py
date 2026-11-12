"""PO-4: scan and intake backend ("Connect a provider" steps 4-6). Written from the AC and interface contract only.

Interface contract the builder follows (beyond the API in the plan):
- backend.discovery.launch_job(connection_id, scan_id, **kw) -> {"job_id": str, "job_url": str}   (LIVE scans; called as
  a module attribute at request time so tests can monkeypatch it)
- backend.discovery.schedule_job(connection_id, schedule, **kw) -> any   (called by PUT /api/connections/{id}/schedule
  for a live connection with "daily"; module attribute, monkeypatchable)
- backend.discovery.run_sync(connection_id) -> any   (the scheduled-sync entry point; runs a scan, never imports)
- ACT_SCAN_INLINE=1 runs every scan in-process and synchronously enough that polling reaches a final state.
- GET /api/scans/{id}/staged -> a list, or {"rows"|"staged"|"items": [...]}. Row keys: id, class
  (new|changed|unchanged|missing), platform, native_id, name, diff, suggestion, sample, imported_agent_id.
  suggestion = {"suggestions": {<7 criteria>: {...score...}}, "tier": n} (the PO-2 /api/suggest/scores shape).
  diff for Changed is a list or dict that names the changed fields (e.g. "tools").
- counts: found == new + changed + unchanged; missing is separate. Final scan state is "done".
- Awaiting: GET /api/connections/{id}/awaiting -> a list or {"rows"|"awaiting"|"items": [...]} of staged rows.
- Import calls backend.governance.ensure_bundle(agent_id) for each imported agent (the Governance bundle).
- Agents imported from a sample connection carry record["sample"] == True; evidence pack generation refuses them
  (POST /api/agents/{id}/evidence-pack answers 404, 409 or 422 and writes no pack).
- Findings for sync changes: findings.type (or rule) == "source_changed" / "source_missing".
"""
import importlib
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

H = {"X-Domino-User-Id": "u-owner", "X-Domino-User": "owner"}
CRITERIA = ["decision_impact", "financial_exposure", "customer_impact", "autonomy",
            "data_sensitivity", "reversibility", "regulatory_exposure"]
LEAVES = ["aws:acct:1234-5678-9012:us-east-1", "aws:acct:1234-5678-9012:eu-west-1",
          "aws:acct:2100-4400-7788:us-east-1"]
ROLE = "arn:aws:iam::123456789012:role/ACT-ReadOnly"


def _purge():
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


@pytest.fixture
def ctx(db_env, tmp_path, monkeypatch):
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "owner")
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("ACT_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")
    monkeypatch.setenv("ACT_SAMPLE_TRUST_DELAY_S", "0")
    monkeypatch.setenv("ACT_SCAN_INLINE", "1")
    for p in ("AWS", "GOOGLE", "MICROSOFT", "DATABRICKS"):
        monkeypatch.delenv(f"ACT_LIVE_{p}", raising=False)
    _purge()
    gov = importlib.import_module("backend.governance")
    calls = []
    monkeypatch.setattr(gov, "ensure_bundle", lambda aid, *a, **k: calls.append(aid) or f"bundle-{aid}")
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield type("Ctx", (), {"c": c, "bundles": calls, "mp": monkeypatch})
    _purge()


# ---------------------------------------------------------------- helpers
def seed_replay():
    """What intake does for the AWS replay fixtures: normalize + upsert. Returns the native ids."""
    from adapters.aws import AwsAdapter
    from backend import db
    from backend.normalize import apply_record, normalize
    sas = AwsAdapter().list_agents_replay()
    with db.engine.begin() as cx:
        for sa in sas:
            apply_record(cx, normalize(sa))
    return {sa.native_id for sa in sas}


def tenant_arns():
    from backend.connections import sample
    return {a["getAgentRuntime"]["agentRuntimeArn"] for a in sample.tenant("aws")["agents"]}


def sample_conn(c, provider="aws"):
    r = c.post("/api/connections", json={"provider": provider, "sample": True}, headers=H)
    assert r.status_code in (200, 201), r.text
    cid = r.json()["id"]
    assert c.get(f"/api/connections/{cid}", headers=H).json()["state"] == "trusted"
    c.post(f"/api/connections/{cid}/check", headers=H)
    r = c.post(f"/api/connections/{cid}/check", headers=H)
    assert r.json()["state"] == "verified", r.text
    return cid


def live_conn(c):
    cid = c.post("/api/connections", json={"provider": "aws"}, headers=H).json()["id"]
    r = c.post(f"/api/connections/{cid}/paste", json={"text": ROLE}, headers=H)
    assert r.json()["recognised"]
    return cid


def start(c, cid):
    r = c.post(f"/api/connections/{cid}/scans", headers=H)
    assert r.status_code in (200, 201, 202), r.text
    return r.json()


def wait(c, sid, timeout=10):
    end = time.time() + timeout
    while True:
        s = c.get(f"/api/scans/{sid}", headers=H).json()
        if s["state"] not in ("queued", "running", "pending", "starting") or time.time() > end:
            return s
        time.sleep(0.05)


def scan(c, cid):
    sid = start(c, cid)["scan_id"]
    s = wait(c, sid)
    assert s["state"] == "done", s
    return sid, s


def rows(c, sid, cls=None):
    r = c.get(f"/api/scans/{sid}/staged", params={"class": cls} if cls else {}, headers=H)
    assert r.status_code == 200, r.text
    b = r.json()
    if isinstance(b, dict):
        b = next(b[k] for k in ("rows", "staged", "items") if k in b)
    return b


def agent_row(aid):
    from backend import db
    with db.engine.connect() as cx:
        return cx.execute(select(db.agents).where(db.agents.c.id == aid)).first()


def import_rows(c, sid, rs, owner="owner-x"):
    for r in rs:
        assert c.patch(f"/api/staged/{r['id']}", json={"owner": owner}, headers=H).status_code == 200
    resp = c.post(f"/api/scans/{sid}/import", json={"ids": [r["id"] for r in rs]}, headers=H)
    assert resp.status_code == 200, resp.text
    return resp.json()


def patch_discover(ctx, fn):
    from adapters.aws import AwsAdapter
    orig = AwsAdapter.discover

    def wrapper(self, cfg, creds, scopes, progress=None):
        return fn(orig(self, cfg, creds, scopes, progress), scopes)
    ctx.mp.setattr(AwsAdapter, "discover", wrapper)


# ---------------------------------------------------------------- PO-4.1
def test_ac1_sample_scan_counts_and_classes(ctx):
    c = ctx.c
    replay = seed_replay()
    cid = sample_conn(c)
    started = start(c, cid)
    assert started["sample"] is True and started["scan_id"]
    s = wait(c, started["scan_id"])
    assert s["state"] == "done"
    n = s["counts"]
    assert n["found"] == n["new"] + n["changed"] + n["unchanged"]
    assert s["updated_at"] and isinstance(s["progress"], list)
    rs = rows(c, started["scan_id"])
    by = lambda k: {r["native_id"] for r in rs if r["class"] == k}  # noqa: E731
    assert by("new") == tenant_arns()
    assert by("unchanged") == replay
    assert not by("changed")
    assert n["new"] == len(by("new")) and n["unchanged"] == len(by("unchanged"))
    assert all(r["sample"] for r in rs if r["class"] == "new")


def test_ac1_staged_rows_carry_suggestions(ctx):
    c = ctx.c
    cid = sample_conn(c)
    sid, _ = scan(c, cid)
    new = rows(c, sid, "new")
    assert new
    for r in new:
        sug = r["suggestion"]
        assert set(sug["suggestions"]) == set(CRITERIA)
        assert all(1 <= v["score"] <= 5 for v in sug["suggestions"].values())
        assert sug["tier"] in (1, 2, 3, "1", "2", "3")


def test_ac1_live_scan_launches_domino_job(ctx):
    """Monkeypatches backend.discovery.launch_job(connection_id, scan_id, **kw) -> {job_id, job_url}."""
    ctx.mp.delenv("ACT_SCAN_INLINE")
    from backend import discovery
    calls = []
    ctx.mp.setattr(discovery, "launch_job",
                   lambda *a, **k: calls.append((a, k)) or {"job_id": "job-42", "job_url": "https://d/jobs/job-42"})
    cid = live_conn(ctx.c)
    r = start(ctx.c, cid)
    assert r["job_id"] == "job-42" and r["job_url"] == "https://d/jobs/job-42" and r["sample"] is False
    assert len(calls) == 1
    assert ctx.c.get(f"/api/scans/{r['scan_id']}", headers=H).json()["state"] == "running"


def test_ac1_one_failing_scope_does_not_stop_the_others(ctx):
    c = ctx.c
    from adapters.aws import AwsAdapter
    orig = AwsAdapter.discover

    def discover(self, cfg, creds, scopes, progress=None):
        if any("eu-west-1" in s for s in scopes):
            raise RuntimeError("boom in eu-west-1")
        return orig(self, cfg, creds, scopes, progress) if any("1234-5678-9012:us-east-1" in s for s in scopes) else []
    ctx.mp.setattr(AwsAdapter, "discover", discover)
    cid = sample_conn(c)
    assert c.put(f"/api/connections/{cid}/scopes", json={"selected": LEAVES}, headers=H).status_code == 200
    sid = start(c, cid)["scan_id"]
    s = wait(c, sid)
    assert s["state"] in ("done", "partial")
    prog = {p["scope"]: p for p in s["progress"]}
    bad = next(v for k, v in prog.items() if "eu-west-1" in k)
    assert bad["errors"]
    others = [v for k, v in prog.items() if "eu-west-1" not in k]
    assert others and not any(v["errors"] for v in others)
    assert s["counts"]["found"] > 0


# ---------------------------------------------------------------- PO-4.2
def _import_new(c, cid):
    sid, _ = scan(c, cid)
    new = rows(c, sid, "new")
    out = import_rows(c, sid, new)
    return sid, new, out


def test_ac2_rescan_without_change_has_no_new_or_changed(ctx):
    c = ctx.c
    seed_replay()
    cid = sample_conn(c)
    _import_new(c, cid)
    sid, s = scan(c, cid)
    assert s["counts"]["new"] == 0 and s["counts"]["changed"] == 0 and s["counts"]["missing"] == 0
    assert s["counts"]["unchanged"] == s["counts"]["found"] > 0
    assert not rows(c, sid, "new") and not rows(c, sid, "changed")


def test_ac2_changed_tools_show_field_diff(ctx):
    c = ctx.c
    cid = sample_conn(c)
    _import_new(c, cid)

    def add_tool(agents, scopes):
        agents[-1].tools = list(agents[-1].tools) + ["brand_new_tool"]
        add_tool.native = agents[-1].native_id
        return agents
    patch_discover(ctx, add_tool)
    sid, s = scan(c, cid)
    ch = rows(c, sid, "changed")
    assert s["counts"]["changed"] == 1 and len(ch) == 1
    assert ch[0]["native_id"] == add_tool.native
    assert "tools" in str(ch[0]["diff"]) and "brand_new_tool" in str(ch[0]["diff"])


def test_ac2_removed_agent_is_missing(ctx):
    c = ctx.c
    cid = sample_conn(c)
    _, new, _ = _import_new(c, cid)
    gone = sorted(tenant_arns())[0]
    patch_discover(ctx, lambda agents, scopes: [a for a in agents if a.native_id != gone])
    sid, s = scan(c, cid)
    assert {r["native_id"] for r in rows(c, sid, "missing")} == {gone}
    assert s["counts"]["missing"] == 1


# ---------------------------------------------------------------- PO-4.3
def test_ac3_import_creates_pending_review_agents(ctx):
    c = ctx.c
    cid = sample_conn(c)
    sid, new, out = _import_new(c, cid)
    assert out["imported"] == len(new) == 2 and out["missing"] == 0 and out["no_owner"] == 0
    assert len(out["agent_ids"]) == 2
    from backend import db
    for aid in out["agent_ids"]:
        a = agent_row(aid)
        assert a.status == "pending_review" and a.connection_id == cid
        assert a.registered_by == "u-owner" and a.owner == "owner-x"
        assert a.tier and a.record["manifest"] and a.record["identity"] and "dependencies" in a.record
        assert aid in ctx.bundles
    with db.engine.connect() as cx:
        ev = [r for r in cx.execute(select(db.audit_events)).all() if r.action == "imported"]
    assert ev and all(e.actor_user_id == "u-owner" for e in ev)
    blob = " ".join(str(e.detail) for e in ev)
    assert cid in blob and sid in blob
    assert {r["imported_agent_id"] for r in rows(c, sid, "new")} == set(out["agent_ids"])


def test_ac3_override_needs_reason_and_changes_tier(ctx):
    from backend.tiering import score_tier
    c = ctx.c
    cid = sample_conn(c)
    sid, _ = scan(c, cid)
    row = rows(c, sid, "new")[0]
    base = {k: v["score"] for k, v in row["suggestion"]["suggestions"].items()}
    hi, lo = score_tier({k: 5 for k in CRITERIA}).tier, score_tier({k: 1 for k in CRITERIA}).tier
    score = 5 if str(hi) != str(row["suggestion"]["tier"]) else 1
    want = hi if score == 5 else lo
    assert str(want) != str(row["suggestion"]["tier"])
    r = c.patch(f"/api/staged/{row['id']}", headers=H,
                json={"overrides": {k: {"score": score} for k in CRITERIA}})
    assert r.status_code == 422
    r = c.patch(f"/api/staged/{row['id']}", headers=H, json={"owner": "o", "overrides": {
        k: {"score": score, "reason": "reviewed by owner"} for k in CRITERIA}})
    assert r.status_code == 200, r.text
    out = c.post(f"/api/scans/{sid}/import", json={"ids": [row["id"]]}, headers=H).json()
    assert out["imported"] == 1
    assert str(agent_row(out["agent_ids"][0]).tier) == str(want)
    assert base  # suggestions existed before the override


def test_ac3_changed_agent_with_higher_tier_returns_to_review(ctx):
    c = ctx.c
    cid = sample_conn(c)
    _, new, out = _import_new(c, cid)
    from backend import db
    from sqlalchemy import update
    aid = out["agent_ids"][0]
    with db.engine.begin() as cx:
        cx.execute(update(db.agents).where(db.agents.c.id == aid).values(status="approved", tier="1"))
    native = agent_row(aid).native_id

    def bump(agents, scopes):
        for a in agents:
            if a.native_id == native:
                a.tools = list(a.tools) + ["wire_transfer_funds"]
        return agents
    patch_discover(ctx, bump)
    sid, _ = scan(c, cid)
    ch = rows(c, sid, "changed")
    assert [r["native_id"] for r in ch] == [native]
    assert int(ch[0]["suggestion"]["tier"]) > 1
    res = import_rows(c, sid, ch)
    assert res["updated"] == 1 and res["imported"] == 0
    a = agent_row(aid)
    assert a.status == "pending_review" and int(a.tier) > 1


# ---------------------------------------------------------------- PO-4.4
def test_ac4_put_schedule_calls_scheduled_job(ctx):
    """Monkeypatches backend.discovery.schedule_job(connection_id, schedule, **kw)."""
    from backend import discovery
    calls = []
    ctx.mp.setattr(discovery, "schedule_job", lambda *a, **k: calls.append((a, k)) or {"job_id": "sched-1"})
    cid = live_conn(ctx.c)
    r = ctx.c.put(f"/api/connections/{cid}/schedule", json={"schedule": "daily"}, headers=H)
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and cid in calls[0][0] and "daily" in calls[0][0]
    assert ctx.c.put(f"/api/connections/{cid}/schedule", json={"schedule": "weekly"}, headers=H).status_code in (400, 422)


def _awaiting(c, cid):
    b = c.get(f"/api/connections/{cid}/awaiting", headers=H).json()
    return next(b[k] for k in ("rows", "awaiting", "items") if k in b) if isinstance(b, dict) else b


def test_ac4_sync_puts_new_agents_in_awaiting_and_imports_nothing(ctx):
    """Calls the sync entry point backend.discovery.run_sync(connection_id) directly."""
    from backend import db, discovery
    c = ctx.c
    cid = sample_conn(c)
    assert _awaiting(c, cid) == []
    discovery.run_sync(cid)
    aw = _awaiting(c, cid)
    assert {r["native_id"] for r in aw} == tenant_arns()
    with db.engine.connect() as cx:
        assert cx.execute(select(db.agents).where(db.agents.c.connection_id == cid)).first() is None


def test_ac4_sync_changes_and_disappearances_create_findings(ctx):
    from backend import db, discovery
    c = ctx.c
    cid = sample_conn(c)
    _, new, out = _import_new(c, cid)
    arns = sorted(tenant_arns())
    gone, chg = arns[0], arns[1]

    def mutate(agents, scopes):
        agents = [a for a in agents if a.native_id != gone]
        for a in agents:
            if a.native_id == chg:
                a.tools = list(a.tools) + ["new_tool_x"]
        return agents
    patch_discover(ctx, mutate)
    discovery.run_sync(cid)
    from backend.normalize import act_id
    with db.engine.connect() as cx:
        fs = cx.execute(select(db.findings)).all()
    kinds = {(f.agent_id, f.type or f.rule) for f in fs}
    assert (act_id("aws", gone), "source_missing") in kinds
    assert (act_id("aws", chg), "source_changed") in kinds


def test_ac4_sample_agents_flagged_and_excluded_from_evidence(ctx):
    from backend import db
    c = ctx.c
    cid = sample_conn(c)
    _, new, out = _import_new(c, cid)
    aid = out["agent_ids"][0]
    assert agent_row(aid).record.get("sample") is True
    r = c.post(f"/api/agents/{aid}/evidence-pack", headers=H)
    assert r.status_code in (404, 409, 422), r.text
    with db.engine.connect() as cx:
        assert cx.execute(select(db.evidence_packs).where(db.evidence_packs.c.agent_id == aid)).first() is None


# ---------------------------------------------------------------- fold-ins
def test_foldin_adapterbase_default_coverage_is_empty():
    from adapters.base import AdapterBase

    class Bare(AdapterBase):
        platform = "bare"
    assert Bare().coverage({}, {}) == []
    assert Bare().coverage({}, {}, ["s1"]) == []


def test_foldin_microsoft_mints_three_audience_tokens():
    from urllib.parse import parse_qs
    from backend.connections import federation
    scopes = []

    def send(method, url, headers, body):
        sc = parse_qs(body)["scope"][0] if isinstance(body, str) else body["scope"]
        scopes.append(sc)
        return {"access_token": "tok-" + sc, "expires_in": 3600}
    out = federation.exchange("microsoft", {"tenant_id": "t", "client_id": "c"}, send=send, token_getter=lambda: "dom")
    toks = [out["arm_token"], out["foundry_token"], out["graph_token"]]
    assert len(set(toks)) == 3
    assert "management.azure.com" in out["arm_token"]
    assert "ai.azure.com" in out["foundry_token"]
    assert "graph.microsoft.com" in out["graph_token"]


@pytest.mark.parametrize("key", ["host", "databricks_url"])
def test_foldin_databricks_workspace_hosts_fall_back_to_cfg_host(key):
    from adapters.databricks import DatabricksAdapter
    host = "https://adb-123.cloud.databricks.com"
    hosts = DatabricksAdapter()._workspace_hosts({key: host}, {})
    assert list(hosts.values()) == [host]
