"""Demo mode: one switch (ACT_DEMO_MODE=1) that runs the whole demo without the live prerequisites.

Everything demo-specific hangs off this module so the stand-ins stay behind clear seams:
  * enabled() / apply_defaults()   the switch and the environment defaults (applied on `import backend`)
  * stand_ins()                    the labelled list /api/health and the UI banner show
  * backend/demo_governance.py     the simulated Governance store (selected in backend/governance.py)
  * backend/routes/demo.py         /api/demo/* (mounted only in demo mode)
  * ensure_seeded() / reset()      in-process seeding of the demo state
Off by default: with ACT_DEMO_MODE unset nothing here changes any behaviour.
"""
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = Path("/mnt/data/ai-control-tower/act")

SERVICE_ACCOUNT = "svc-act-recon-demo"
APPROVER_ID = "demo-approver-simulated"
APPROVER_NAME = "demo-approver (simulated second user)"
GOVERNANCE_LABEL = "simulated Governance (demo)"
SERVICE_ACCOUNT_LABEL = "simulated service account (demo)"
CLASSIFIER_LABEL = "demo classifier (not the endpoint)"
BANNER_TEXT = "Demo mode: simulated data and stand-ins"

STAND_INS = [
    {"name": "llm", "label": "mock LLM (no gateway call)"},
    {"name": "recon_agent_hosting", "label": "recon agent runs in-process (not a deployed Domino Agent)"},
    {"name": "governance", "label": GOVERNANCE_LABEL},
    {"name": "classifier", "label": CLASSIFIER_LABEL},
    {"name": "service_account", "label": SERVICE_ACCOUNT_LABEL},
    {"name": "second_approver", "label": "simulated second approver (demo-approver-simulated)"},
]

_ENV_DEFAULTS = {
    "ACT_LLM_PROVIDER": "mock",
    "ACT_DOMINO_LOCAL_AGENTS": "config/local_agents.json",
    "RECON_AGENT_ID": "local-recon-agent",
    "ACT_EXTERNAL_ALLOW_HOSTS": "127.0.0.1,localhost",
}

_applied: list = []


def enabled() -> bool:
    return (os.environ.get("ACT_DEMO_MODE") or "").strip().lower() in ("1", "true", "yes", "on")


def default_db_path() -> str:
    """A separate demo DB, never the shared act.db."""
    if DATASET_DIR.is_dir():
        return str(DATASET_DIR / "demo.db")
    return str(ROOT / ".act-demo" / "act.db")


def apply_defaults() -> None:
    """Fill the demo defaults where a variable is unset (set variables are never overridden)."""
    if not enabled():
        return

    def fill(k, v):
        if not os.environ.get(k):
            os.environ[k] = v
            _applied.append(k)
    for k, v in _ENV_DEFAULTS.items():
        fill(k, v)
    if not os.environ.get("ACT_DB"):
        fill("ACT_DB", default_db_path())
    if not os.environ.get("MLFLOW_TRACKING_URI"):  # keep tracking local: no outbound call
        fill("MLFLOW_TRACKING_URI", f"file://{Path(os.environ['ACT_DB']).resolve().parent / 'mlruns'}")


def restore_defaults() -> None:
    """Remove the defaults apply_defaults() filled in (called when the app shuts down)."""
    while _applied:
        os.environ.pop(_applied.pop(), None)


def stand_ins() -> list:
    return [dict(s) for s in STAND_INS] if enabled() else []


def service_account_identity() -> dict:
    return {"type": "domino_service_account", "principal": SERVICE_ACCOUNT, "distinct": True,
            "simulated": True, "label": SERVICE_ACCOUNT_LABEL}


def _seed(**kw):
    from jobs import seed_demo
    with seed_demo._env(ACT_URL=""):  # bundles are created in-process on the simulated store
        return seed_demo.seed(governance=True, **kw)


def ensure_seeded():
    """Seed an empty DB to the demo state. Idempotent: a populated DB is left alone."""
    from sqlalchemy import func, select
    from . import db
    db.init_db()
    with db.engine.connect() as cx:
        n = cx.execute(select(func.count()).select_from(db.agents)).scalar() or 0
    if n:
        return None
    log.warning("demo mode: seeding an empty database (%s)", db.DB_PATH)
    return _seed()


def reset():
    """Wipe the demo DB and reseed it to the initial demo state."""
    from . import db, demo_governance
    with demo_governance.lock:
        db.metadata.drop_all(db.engine)
        demo_governance.clear()
    db.init_db()
    return _seed()
