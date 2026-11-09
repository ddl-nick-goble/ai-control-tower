"""Build the demo DB for the console e2e tests (in-process, no server needed)."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
POLICIES = ["ACT 2026-54 Agentic Governance", "SR 26-2 Model Risk (slim)"]
RECON_NATIVE = "recon-agent"


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

    aid = act_id("domino", RECON_NATIVE)
    ts = now_iso()
    manifest = {"tools": ["fetch_ledger", "compare_entries", "propose_adjustment"],
                "data_scopes": ["finance.ledger"], "systems": [], "value_limits": {}}
    rec = {"id": aid, "name": RECON_NATIVE, "owner": "seed-user", "tier": 2, "status": "discovered",
           "source_platform": "domino", "manifest": manifest, "last_seen": ts}
    with db.engine.begin() as cx:
        cx.execute(db.agents.insert().values(
            id=aid, source_platform="domino", native_id=RECON_NATIVE, name=RECON_NATIVE, owner="seed-user",
            tier="2", tier_reasoning="Seeded demo agent: ledger reconciliation with adjustment proposals.",
            criteria={}, status="discovered", registered_by="seed-user", registered_by_username="seed-user",
            last_seen=ts, record=rec, bundle_id="bundle-recon-demo-1",
            governance={"policies": POLICIES, "stage": "draft"}))
        cx.execute(db.manifests.insert().values(agent_id=aid, version="1", content=manifest, created_at=ts))
        cx.execute(db.dependencies.insert().values(
            agent_id=aid, kind="model", name="break_classifier", target="5",
            record={"name": "break_classifier", "registry_version": "5"}))

    client = TestClient(backend.main.app)
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
    db.engine.dispose()
    return {"recon_id": aid, "treasury_id": tid, "run_id": run_id}


if __name__ == "__main__":
    print(json.dumps(seed(sys.argv[1] if len(sys.argv) > 1 else "/tmp/act-e2e/act.db")))
