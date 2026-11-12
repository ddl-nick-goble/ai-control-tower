"""Build the demo DB for the console e2e tests (in-process, no server needed)."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
POLICIES = ["ACT 2026-54 Agentic Governance", "SR 26-2 Model Risk (slim)"]
RECON_NATIVE = "recon-agent"
RECON_CRITERIA = {}  # filled from config/tiering.yaml at seed time
SECOND_TRACE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _attr(k, v):
    return {"key": k, "value": {"intValue": str(v)} if isinstance(v, int) else {"stringValue": v}}


def _second_run_doc():
    t0 = 1794100000  # 2026-11-08, a different day from the weekend-drift run
    def span(i, name, off, attrs, kind=1):
        d = {"traceId": SECOND_TRACE, "spanId": f"{i:016x}", "name": name, "kind": kind,
             "startTimeUnixNano": str((t0 + off) * 10**9), "endTimeUnixNano": str((t0 + off + 1) * 10**9),
             "attributes": [_attr(k, v) for k, v in attrs], "status": {"code": 1}}
        if i != 1:
            d["parentSpanId"] = f"{1:016x}"
        return d
    spans = [
        span(1, "invoke_agent anthropic.claude-sonnet-4", 0, [
            ("gen_ai.operation.name", "invoke_agent"), ("gen_ai.agent.id", RECON_NATIVE),
            ("gen_ai.agent.name", "recon-agent"),
            ("gen_ai.provider.name", "aws.bedrock"),
            ("gen_ai.input.messages", json.dumps([{"role": "user", "content": "Reconcile the ledger."}])),
            ("gen_ai.output.messages", json.dumps([{"role": "assistant", "content": "Adjustment proposed."}]))]),
        span(2, "chat anthropic.claude-sonnet-4", 1, [
            ("gen_ai.operation.name", "chat"), ("gen_ai.request.model", "anthropic.claude-sonnet-4"),
            ("gen_ai.usage.input_tokens", 900), ("gen_ai.usage.output_tokens", 120)], kind=3),
        span(3, "execute_tool fetch_ledger", 2, [
            ("gen_ai.operation.name", "execute_tool"), ("gen_ai.tool.name", "fetch_ledger"),
            ("gen_ai.tool.call.arguments", json.dumps({"account": "OPS-USD-001"})),
            ("gen_ai.tool.call.result", json.dumps({"entries": 42}))]),
        span(4, "execute_tool propose_adjustment", 3, [
            ("gen_ai.operation.name", "execute_tool"), ("gen_ai.tool.name", "propose_adjustment"),
            ("gen_ai.tool.call.arguments", json.dumps(
                {"account": "OPS-USD-001", "amount": "5 mln", "currency": "USD"})),
            ("gen_ai.tool.call.result", json.dumps({"status": "proposed"}))]),
    ]
    return {"resourceSpans": [{"resource": {"attributes": [
        _attr("service.name", "recon-agent"), _attr("cloud.provider", "aws"),
        _attr("cloud.platform", "aws_bedrock_agentcore")]},
        "scopeSpans": [{"scope": {"name": "opentelemetry.instrumentation.genai", "version": "1.0.0"},
                        "spans": spans}]}]}


def seed(db_path: str) -> dict:
    """Create the DB at db_path; return {recon_id, treasury_id, run_id}."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    for ext in ("", "-wal", "-shm"):
        Path(db_path + ext).unlink(missing_ok=True)
    os.environ["ACT_DB"] = db_path
    os.environ["ACT_DB_PREWARM_COUNT"] = "0"
    os.environ["DOMINO_STARTING_USERNAME"] = "seed-user"
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import backend.main  # noqa: F401  (loads routers and the drift hook)
    from backend import db
    from backend.normalize import act_id, now_iso
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    db.init_db()
    from jobs.intake import run_intake
    run_intake()

    import yaml
    global RECON_CRITERIA
    spec = yaml.safe_load((ROOT / "config" / "tiering.yaml").read_text())["criteria"]
    RECON_CRITERIA = {k: 3 for k in spec}
    aid = act_id("domino", RECON_NATIVE)
    ts = now_iso()
    manifest = {"tools": ["fetch_ledger", "compare_entries", "propose_adjustment"],
                "data_scopes": ["finance.ledger"], "systems": [],
                "value_limits": {"propose_adjustment": {"amount": 10000, "currency": "USD"}}}
    rec = {"id": aid, "name": RECON_NATIVE, "owner": "seed-user", "tier": 2, "status": "discovered",
           "source_platform": "domino", "manifest": manifest, "last_seen": ts}
    with db.engine.begin() as cx:
        cx.execute(db.agents.insert().values(
            id=aid, source_platform="domino", native_id=RECON_NATIVE, name=RECON_NATIVE, owner="seed-user",
            tier="2", tier_reasoning="Seeded demo agent: ledger reconciliation with adjustment proposals.",
            criteria={}, status="discovered", registered_by="seed-user", registered_by_username="seed-user",
            last_seen=ts, record=rec, bundle_id="bundle-recon-demo-1",
            governance={"policies": POLICIES, "stage": "draft"}))
        rec["identity"] = {"type": "domino_service_account", "principal": "svc-act-recon", "distinct": True}
        cx.execute(db.agents.update().where(db.agents.c.id == aid).values(record=rec))
        cx.execute(db.manifests.insert().values(agent_id=aid, version="1", content=manifest, created_at=ts))
        cx.execute(db.dependencies.insert().values(
            agent_id=aid, kind="model", name="break_classifier", target="5",
            record={"name": "break_classifier", "registry_version": "5", "alias": "champion"}))

    client = TestClient(backend.main.app)
    hdr = {"X-Domino-User-Id": "seed-user", "X-Domino-User": "seed-user"}
    r = client.post(f"/api/agents/{aid}/tier", headers=hdr, json={"criteria": RECON_CRITERIA})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/agents/{aid}/validation", headers=hdr, json={
        "mean": 0.825, "p5": 0.4, "p95": 0.95, "pass_rate": 0.8, "n_prompts": 20, "reps": 5, "threshold": 0.7,
        "judge_alias": "mock", "judge_model_id": "mock-llm", "experiment": "validate-e2e"})
    assert r.status_code in (200, 201), r.text
    spans_doc = json.loads((ROOT / "fixtures" / "runs" / "weekend-drift.json").read_text())
    r = client.post("/v1/traces", json=spans_doc,
                    headers={"X-Domino-User-Id": "seed-user", "X-Domino-User": "seed-user"})
    assert r.status_code == 200, r.text
    with db.engine.connect() as cx:
        t = cx.execute(select(db.agents.c.id).where(db.agents.c.name == "treasury-sweep-agent")).first()
    tid = t[0]
    deadline = time.time() + 15
    run_id = None
    while time.time() < deadline:
        fs = client.get(f"/api/agents/{tid}/findings").json()
        if fs:
            run_id = fs[0]["run_id"]
            break
        time.sleep(0.2)
    assert run_id, "drift finding was not created"
    r = client.post("/v1/traces", json=_second_run_doc(), headers=hdr)
    assert r.status_code == 200, r.text
    time.sleep(1.5)  # let drift evaluation for the second run settle
    with db.engine.connect() as cx:
        k = cx.execute(select(db.agents.c.id).where(db.agents.c.name == "kyc-refresh-agent")).first()
    db.engine.dispose()
    return {"recon_id": aid, "treasury_id": tid, "run_id": run_id, "run2_id": SECOND_TRACE,
            "approvable_id": k[0]}


if __name__ == "__main__":
    print(json.dumps(seed(sys.argv[1] if len(sys.argv) > 1 else "/tmp/act-e2e/act.db")))
