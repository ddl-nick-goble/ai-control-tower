"""One command from a cold start to the demo state.

    python jobs/seed_demo.py [--governance]        (also runs as a Domino Job)

Steps: init_db, intake (12 agents), one in-process recon-agent run with its ACT traces ingested in-process,
the weekend-drift fixture (treasury-sweep-agent scope_drift), one validation record (5 repetitions), and with
--governance a Domino Governance bundle for recon-agent and treasury-sweep-agent (best effort). Idempotent:
a second run changes nothing that already exists. It never approves anything.

Env: ACT_DB, ACT_LLM_PROVIDER (default mock when LLM_GATEWAY_URL is unset), ACT_DOMINO_LOCAL_AGENTS
(default config/local_agents.json), DOMINO_STARTING_USERNAME (registering user; default "seed-user").
"""
import argparse
import contextlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("seed_demo")
RECON_NATIVE_ID = "local-recon-agent"
GOVERNANCE_AGENTS = ("recon-agent", "treasury-sweep-agent")
SECOND_APPROVER_ID = "demo-approver-simulated"
SECOND_APPROVER_NAME = "demo-approver (simulated second user)"


@contextlib.contextmanager
def _env(**pairs):
    """Set env vars for the block (None leaves it alone) and restore them afterwards."""
    old = {k: os.environ.get(k) for k in pairs}
    try:
        for k, v in pairs.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _agent_ids():
    from backend import db
    with db.engine.connect() as cx:
        return {r.name: r.id for r in cx.execute(db.agents.select())}


def _count(table, agent_id):
    from backend import db
    from sqlalchemy import func, select
    with db.engine.connect() as cx:
        return cx.execute(select(func.count()).select_from(table).where(table.c.agent_id == agent_id)).scalar() or 0


_recon_lock = threading.Lock()


def run_recon_once(agent_id) -> str:
    """Run the recon agent once in-process (always) and return the id of the new run."""
    from backend import db
    from sqlalchemy import select

    def run_ids():
        with db.engine.connect() as cx:
            return {r[0] for r in cx.execute(select(db.runs.c.id).where(db.runs.c.agent_id == agent_id))}
    with _recon_lock:
        before = run_ids()
        _run_recon(agent_id, {}, force=True)
        new = run_ids() - before
    if not new:
        raise RuntimeError("the recon run produced no run in ACT")
    return sorted(new)[-1]


def _run_recon(agent_id, summary, force=False):
    """Run the recon agent once; its spans go to the ACT ingest in-process instead of over HTTP."""
    from backend import db
    if not force and _count(db.runs, agent_id):
        summary["recon_run"] = "skipped (already has runs)"
        return
    from agents.recon_agent import act_export
    from backend.otlp import parse_request
    from backend.routes.traces import ingest

    def flush(self):
        if not self.spans:
            return False
        ingest(parse_request(json.dumps(self.payload()).encode(), "application/json"))
        return True

    orig = act_export.ActTraceBuffer.flush
    act_export.ActTraceBuffer.flush = flush
    try:
        from agents.recon_agent.agent import run
        out = run("Reconcile the GL ledger against the custodian ledger and list every break with its classification.")
    finally:
        act_export.ActTraceBuffer.flush = orig
    summary["recon_run"] = f"ok ({len(out.get('breaks', []))} breaks, {len(out.get('proposals', []))} proposals)"


def _ingest_drift(summary):
    from backend import db
    from backend.otlp import parse_request
    from backend.routes.traces import ingest
    ids = _agent_ids()
    tid = ids.get("treasury-sweep-agent")
    if tid and _count(db.findings, tid):
        summary["weekend_drift"] = "skipped (findings exist)"
        return
    p = ROOT / "fixtures" / "runs" / "weekend-drift.json"
    ingest(parse_request(p.read_bytes(), "application/json"))
    summary["weekend_drift"] = "ingested"


class _NoMlflow:
    """Stand-in for the mlflow module when no tracking server is reachable."""

    class _Run:
        class info:
            run_id = "none"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def set_experiment(self, *a, **k): pass
    def start_run(self, *a, **k): return self._Run()
    def log_param(self, *a, **k): pass
    def log_metric(self, *a, **k): pass


def _validate(agent_id, summary):
    from backend import db
    if _count(db.validation_records, agent_id):
        summary["validation"] = "skipped (record exists)"
        return
    from jobs import validate
    from backend import demo
    try:
        if demo.enabled():  # demo mode keeps validation local: no MLflow server round trips
            raise ConnectionError("demo mode")
        res = validate.run_validation(validate.load_prompts(), reps=5)
        note = ""
    except Exception as e:  # noqa: BLE001 - MLflow tracking unavailable: score without MLflow runs
        log.warning("MLflow tracking unavailable (%s); validating without MLflow runs", type(e).__name__)
        res = validate.run_validation(validate.load_prompts(), reps=5, mlflow_client=_NoMlflow())
        note = " [no MLflow runs]"
    validate.record_in_process(agent_id, res)
    summary["validation"] = (f"mean {res['mean']:.3f} p5 {res['p5']:.3f} p95 {res['p95']:.3f} "
                             f"pass_rate {res['pass_rate']:.1%} (judge {res['judge_model_id']}){note}")


def _governance(summary):
    """Create the bundles (POST /api/agents/{id}/governance equivalent) before any approval. Best effort."""
    ids = _agent_ids()
    out = {}
    for name in GOVERNANCE_AGENTS:
        aid = ids.get(name)
        if not aid:
            out[name] = "failed: agent not found"
            continue
        try:
            if (os.environ.get("ACT_URL") or "").strip():
                import requests
                tok = requests.get(os.environ.get("ACT_TOKEN_URL") or "http://localhost:8899/access-token",
                                   timeout=5).text.strip()
                r = requests.post(f"{os.environ['ACT_URL'].rstrip('/')}/api/agents/{aid}/governance",
                                  headers={"Authorization": f"Bearer {tok}"}, timeout=60)
                r.raise_for_status()
                out[name] = f"bundle {r.json().get('bundle_id')}"
            else:
                from backend import governance
                out[name] = f"bundle {governance.ensure_bundle(aid)}"
        except Exception as e:  # noqa: BLE001 - best effort, reported
            log.warning("governance for %s failed: %s", name, e)
            out[name] = f"failed: {str(e)[:150]}"
    summary["governance"] = out


def seed(db_path=None, governance=False) -> dict:
    """Bring the database to the demo state. Returns a summary dict."""
    summary = {}
    user = os.environ.get("DOMINO_STARTING_USERNAME") or "seed-user"
    explicit_provider = os.environ.get("ACT_LLM_PROVIDER")
    provider = explicit_provider or ("gateway" if os.environ.get("LLM_GATEWAY_URL") else "mock")
    owner = None if os.environ.get("ACT_LOCAL_AGENT_OWNER") else user
    with _env(ACT_DB=str(db_path) if db_path else None, DOMINO_STARTING_USERNAME=user,
              ACT_LOCAL_AGENT_OWNER=owner,
              ACT_LLM_PROVIDER=provider,
              ACT_DOMINO_LOCAL_AGENTS=(os.environ.get("ACT_DOMINO_LOCAL_AGENTS")
                                       or str(ROOT / "config" / "local_agents.json")),
              ACT_INTAKE_INPROCESS="1", RECON_AGENT_ID=RECON_NATIVE_ID):
        summary["llm_provider"] = provider
        summary["registered_by"] = user
        import backend.main  # noqa: F401 - registers the drift detector on trace ingest
        from backend import db
        if db_path and Path(db.DB_PATH).resolve() != Path(db_path).resolve():
            raise RuntimeError(f"backend.db is already bound to {Path(db.DB_PATH).resolve()}, "
                               f"but seed(db_path={Path(db_path).resolve()}) was requested; "
                               f"set ACT_DB before importing backend or seed that path")
        summary["db_path"] = str(Path(db.DB_PATH).resolve())
        db.init_db()
        from jobs.intake import run_intake
        intake = run_intake()
        summary["intake"] = {k: intake[k] for k in intake if k != "errors"}
        summary["intake_errors"] = intake["errors"]
        ids = _agent_ids()
        summary["agents"] = len(ids)
        recon = ids.get("recon-agent")
        if not recon:
            summary["recon_run"] = summary["validation"] = "failed: recon-agent not in the fleet"
        else:
            _run_recon(recon, summary)
        _ingest_drift(summary)
        if recon:
            _validate(recon, summary)
        if governance:
            _governance(summary)
    return summary


def approve_as_second_user(agent_id) -> str:
    """Approve an agent as a simulated second user and return the approval expiry (ISO string).

    Stand-in for a real second Domino user (see docs/INTEGRATION.md): the identity headers are supplied in-process.
    """
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        r = c.post(f"/api/agents/{agent_id}/approve",
                   headers={"X-Domino-User-Id": SECOND_APPROVER_ID, "X-Domino-User": SECOND_APPROVER_NAME})
    if r.status_code != 200:
        raise RuntimeError(f"approval refused: HTTP {r.status_code} {r.text[:200]}")
    return r.json()["expires_at"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seed ACT to the demo state")
    ap.add_argument("--governance", action="store_true", help="also create Domino Governance bundles")
    ap.add_argument("--approve", metavar="AGENT_NAME",
                    help="only approve this agent as the simulated second user (e.g. recon-agent), then exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    from backend import db
    print(f"ACT DB: {Path(db.DB_PATH).resolve()}")
    if args.approve:
        ids = _agent_ids()
        if args.approve not in ids:
            print(f"no agent named {args.approve!r}", file=sys.stderr)
            return 2
        expiry = approve_as_second_user(ids[args.approve])
        time.sleep(2)  # let the background governance mirror finish before the process exits
        print(f"approved {args.approve} as {SECOND_APPROVER_NAME}; expires {expiry}")
        return 0
    if not os.environ.get("LLM_GATEWAY_URL") and not os.environ.get("ACT_LLM_PROVIDER"):
        print("LLM provider: mock (LLM_GATEWAY_URL is not set)")
    s = seed(governance=args.governance)
    print(json.dumps(s, indent=2, default=str))
    bad = s["intake_errors"] or str(s.get("recon_run", "")).startswith("failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
