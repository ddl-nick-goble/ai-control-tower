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
_db_new = False  # ACT_DB was filled by demo defaults, or its file did not exist at startup


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
        if k not in os.environ:
            os.environ[k] = v
            _applied.append(k)
    for k, v in _ENV_DEFAULTS.items():
        fill(k, v)
    global _db_new
    if "ACT_DB" not in os.environ:
        fill("ACT_DB", default_db_path())
    # Record, before anything opens the DB, whether the file is new or demo-owned by default.
    _db_new = "ACT_DB" in _applied or not Path(os.environ["ACT_DB"]).expanduser().exists()
    demo_dir = Path(os.environ["ACT_DB"]).expanduser().resolve().parent
    if "MLFLOW_TRACKING_URI" not in os.environ:  # keep tracking local: no outbound call
        fill("MLFLOW_TRACKING_URI", f"file://{demo_dir / 'mlruns'}")
    if "ACT_EVIDENCE_DIR" not in os.environ:  # demo packs never land beside real packs
        fill("ACT_EVIDENCE_DIR", str(demo_dir / "evidence"))


def effective_llm_provider() -> str:
    return "mock" if (os.environ.get("ACT_LLM_PROVIDER") or "gateway").strip().lower() == "mock" else "gateway"


def live_switches() -> list:
    out = []
    if effective_llm_provider() != "mock":
        out.append("ACT_LLM_PROVIDER=" + (os.environ.get("ACT_LLM_PROVIDER") or "gateway"))
    uri = os.environ.get("MLFLOW_TRACKING_URI") or ""
    if uri and not uri.startswith(("file:", "/")):
        out.append("MLFLOW_TRACKING_URI (non-file)")
    out += sorted(k for k, v in os.environ.items() if k.startswith("ACT_LIVE_") and v.strip())
    return out


def warn_live_switches() -> None:
    sw = live_switches()
    if enabled() and sw:
        log.warning("demo mode is on but live switches are set (%s); those parts are NOT simulated", ", ".join(sw))


def restore_defaults() -> None:
    """Remove the defaults apply_defaults() filled in (called when the app shuts down)."""
    while _applied:
        os.environ.pop(_applied.pop(), None)


def stand_ins() -> list:
    if not enabled():
        return []
    out = [dict(s) for s in STAND_INS]
    if effective_llm_provider() != "mock":
        out[0]["label"] = "LLM via gateway (live; ACT_LLM_PROVIDER set explicitly)"
    return out


def service_account_identity() -> dict:
    return {"type": "domino_service_account", "principal": SERVICE_ACCOUNT, "distinct": True,
            "simulated": True, "label": SERVICE_ACCOUNT_LABEL}


def _seed(**kw):
    from jobs import seed_demo
    with seed_demo._env(ACT_URL=""):  # bundles are created in-process on the simulated store
        return seed_demo.seed(governance=True, **kw)


class DemoGuardError(Exception):
    """Raised when demo seeding/reset is refused because the DB is not demo-owned."""


SHARED_DB = Path("/mnt/data/ai-control-tower/act/act.db")


def _marker_present(cx) -> bool:
    from sqlalchemy import text
    return cx.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='act_demo_marker'")).first() is not None \
        and cx.execute(text("SELECT 1 FROM act_demo_marker")).first() is not None


def _write_marker() -> None:
    from sqlalchemy import text
    from . import db
    with db.engine.begin() as cx:
        cx.execute(text("CREATE TABLE IF NOT EXISTS act_demo_marker (id INTEGER PRIMARY KEY, note TEXT)"))
        cx.execute(text("INSERT OR REPLACE INTO act_demo_marker (id, note) VALUES (1, 'created by ACT demo mode')"))


def _refuse(msg):
    log.warning("demo mode: %s", msg)
    raise DemoGuardError(msg)


def _check_not_shared() -> None:
    from . import db
    try:
        if Path(db.DB_PATH).expanduser().resolve() == SHARED_DB.resolve():
            _refuse(f"refusing: ACT_DB resolves to the shared database {SHARED_DB}; demo mode only uses a demo-owned DB")
    except OSError:
        pass


def _may_claim() -> bool:
    return _db_new or (os.environ.get("ACT_DEMO_ALLOW_DB") or "").strip().lower() in ("1", "true", "yes", "on")


def ensure_seeded():
    """Seed an empty DB to the demo state. Idempotent: a populated demo DB is left alone.
    Raises DemoGuardError for the shared DB, a non-demo DB that already has data, or an unmarked
    DB that demo mode may not claim (a hand-set ACT_DB needs ACT_DEMO_ALLOW_DB=1)."""
    from sqlalchemy import func, select, text
    from . import db
    _check_not_shared()
    db.init_db()
    with db.engine.connect() as cx:
        n = cx.execute(select(func.count()).select_from(db.agents)).scalar() or 0
        marked = _marker_present(cx)
    if n and not marked:
        _refuse(f"refusing to seed {db.DB_PATH}: it already holds non-demo data (no demo marker)")
    if not marked and not _may_claim():
        _refuse(f"refusing to claim {db.DB_PATH} as a demo database: ACT_DB was set by hand; "
                "set ACT_DEMO_ALLOW_DB=1 to let demo mode seed and reset it")
    if n:
        return None
    # Claim + re-check in one transaction: the marker insert takes the write lock, so of two
    # processes only one sees rowcount 1 and seeds; a DB that filled meanwhile rolls the claim back.
    with db.engine.begin() as cx:
        cx.execute(text("CREATE TABLE IF NOT EXISTS act_demo_marker (id INTEGER PRIMARY KEY, note TEXT)"))
        r = cx.execute(text("INSERT OR IGNORE INTO act_demo_marker (id, note) VALUES (1, 'created by ACT demo mode')")).rowcount
        n2 = cx.execute(select(func.count()).select_from(db.agents)).scalar() or 0
        if n2 and r:
            _refuse(f"refusing to seed {db.DB_PATH}: it already holds non-demo data (no demo marker)")
        if n2 or (r == 0 and marked is False):
            return None  # another process seeded / is seeding
    log.warning("demo mode: seeding an empty database (%s)", db.DB_PATH)
    return _seed()


def reset():
    """Wipe the demo DB and reseed it. Only a demo-owned DB (marker present) is ever wiped."""
    from . import db, demo_governance
    _check_not_shared()
    with db.engine.connect() as cx:
        if not _marker_present(cx):
            _refuse(f"refusing to reset {db.DB_PATH}: it is not a demo-owned database (no demo marker)")
    with demo_governance.lock:
        db.metadata.drop_all(db.engine)
        demo_governance.clear()
    db.init_db()
    _write_marker()
    return _seed()
