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
import threading
from pathlib import Path

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = Path("/mnt/data/ai-control-tower/act")

DEMO_AGENT_OWNER = "recon-ops@example-bank.com"
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


_runtime = False  # the in-app Settings switch (no restart); ACT_DEMO_MODE in the environment always wins
_applied_rt: list = []
_rt_lock = threading.RLock()  # held for the whole of a switch; _runtime and db.engine change under it
_switching = False
_state_lock = threading.Lock()  # guards _switching and _inflight, which the request gate reads from other threads
_cond = threading.Condition(_state_lock)
_inflight = 0  # requests currently inside the app (counted by the ASGI gate in main.py)
_switch_mutex = threading.Lock()  # one switch at a time; taken before draining, never while a request waits on _rt_lock
DRAIN_TIMEOUT = 15.0


def env_enabled() -> bool:
    return (os.environ.get("ACT_DEMO_MODE") or "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return env_enabled() or _runtime


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


def _setting(name):
    from .settings import get_setting
    return get_setting(name)


def effective_llm_provider() -> str:
    return "mock" if (_setting("llm_provider") or "gateway").strip().lower() == "mock" else "gateway"


def live_switches() -> list:
    out = []
    if effective_llm_provider() != "mock":
        out.append("ACT_LLM_PROVIDER=" + (_setting("llm_provider") or "gateway"))
    uri = os.environ.get("MLFLOW_TRACKING_URI") or ""
    if uri and not uri.startswith(("file:", "/")):
        out.append("MLFLOW_TRACKING_URI (non-file)")
    out += sorted(k for k, v in os.environ.items() if k.startswith("ACT_LIVE_") and v.strip())
    return out


def warn_live_switches() -> None:
    sw = live_switches()
    if enabled() and sw:
        log.warning("demo mode is on but live switches are set (%s); those parts are NOT simulated", ", ".join(sw))


def _fill_runtime(k, v) -> None:
    if k not in os.environ:
        os.environ[k] = v
        _applied_rt.append(k)


def applied_env() -> set:
    """Variables demo mode filled in itself; Settings treats them as defaults, not as project variables."""
    return set(_applied) | set(_applied_rt)


def switching() -> bool:
    """True while a runtime switch is in progress; the app holds ordinary requests until it ends (see main.py)."""
    with _state_lock:
        return _switching


def _set_switching(v: bool) -> None:
    global _switching
    with _cond:
        _switching = v
        _cond.notify_all()


def enter() -> bool:
    """Gate entry: atomically refuse while a switch runs, else count the request as in flight."""
    global _inflight
    with _cond:
        if _switching:
            return False
        _inflight += 1
        return True


def leave() -> None:
    global _inflight
    with _cond:
        _inflight -= 1
        _cond.notify_all()


def _begin_switch(own: int) -> None:
    """Stop new requests and wait until only `own` (the settings request that triggers the switch) remain.
    Called with _switch_mutex held and _rt_lock NOT held, so a request waiting on _rt_lock cannot deadlock the drain."""
    global _switching
    with _cond:
        _switching = True
        if not _cond.wait_for(lambda: _inflight <= own, timeout=DRAIN_TIMEOUT):
            _switching = False
            _cond.notify_all()
            raise DemoGuardError("requests still running; try again")


class _Switch:
    def __init__(self, own):
        self.own = own

    def __enter__(self):
        global _inflight
        # The triggering request is counted in flight while it waits for another switch; that switch would wait
        # for it to drain (a 15 s stall). Step out of the count while waiting, back in once the mutex is ours.
        if self.own:
            with _cond:
                _inflight -= self.own
                _cond.notify_all()
        try:
            _switch_mutex.acquire()
        except BaseException:
            if self.own:
                with _cond:
                    _inflight += self.own
            raise
        if self.own:
            with _cond:
                _inflight += self.own
        try:
            _begin_switch(self.own)
        except BaseException:
            _switch_mutex.release()
            raise
        return self

    def __exit__(self, *a):
        _set_switching(False)
        _switch_mutex.release()
        return False


from contextlib import contextmanager


def acquire_outside_flight(lock, own=1) -> None:
    """Take `lock` while this request is stepped out of the in-flight count, so the holder's switch never waits
    (the 15 s drain stall) for a request that is itself only waiting for that lock. Counted back in once held."""
    global _inflight
    with _cond:
        _inflight -= own
        _cond.notify_all()
    try:
        lock.acquire()
    finally:
        with _cond:
            _inflight += own


@contextmanager
def active_demo_engine():
    """For demo routes and jobs: hold the switch lock, require demo mode, yield the one engine to use throughout.
    A switch-off cannot run meanwhile, so the real DB is never touched by a demo action."""
    from . import db
    with _rt_lock:
        if not enabled():
            raise DemoGuardError("demo mode is off")
        eng = db.engine
        if eng is db.real_engine and not env_enabled():
            _refuse("refusing: the active database is the real database")
        yield eng


def switch_on(own: int = 0) -> None:
    """Runtime demo switch: seed the demo DB (if empty), then flip to it.
    The active engine and the demo flag change together under one lock, and ordinary requests wait while
    seeding runs (they never see a half-seeded demo DB). A failure restores the previous state exactly and raises
    DemoGuardError when the demo DB may not be used. Single process only: app.sh runs one uvicorn worker."""
    global _runtime, _db_new
    from . import db
    if enabled():
        return
    with _Switch(own), _rt_lock:
        if enabled():
            return
        path = default_db_path()
        if Path(path).expanduser().resolve() == Path(db.REAL_DB_PATH).expanduser().resolve():
            _refuse("refusing: the demo database path is the real database")
        prev_new, applied_before = _db_new, len(_applied_rt)
        _db_new = not Path(path).expanduser().exists()
        # Seeding needs db.engine and the demo stand-ins (simulated Governance, demo owner), so both flip now;
        # the gate in main.py holds every other request until this block ends, success or failure.
        prev = db.swap(db.engine_for(path), path)
        _runtime = True
        try:
            for k, v in _ENV_DEFAULTS.items():
                _fill_runtime(k, v)
            demo_dir = Path(path).expanduser().resolve().parent
            _fill_runtime("MLFLOW_TRACKING_URI", f"file://{demo_dir / 'mlruns'}")
            _fill_runtime("ACT_EVIDENCE_DIR", str(demo_dir / "evidence"))
            ensure_seeded()
        except BaseException:
            _runtime = False
            db.swap(*prev)
            _db_new = prev_new
            while len(_applied_rt) > applied_before:
                os.environ.pop(_applied_rt.pop(), None)
            raise


def switch_off(own: int = 0) -> None:
    """Back to the real DB. The demo engine is kept (not disposed) because in-flight requests may still hold it."""
    global _runtime
    from . import db
    if not _runtime:
        return
    with _Switch(own), _rt_lock:
        if not _runtime:
            return
        db.use_real()
        _runtime = False
        while _applied_rt:
            os.environ.pop(_applied_rt.pop(), None)


def restore_defaults() -> None:
    """Remove the defaults apply_defaults() filled in (called when the app shuts down)."""
    while _applied:
        os.environ.pop(_applied.pop(), None)
    while _applied_rt:  # a shutdown whose switch-off was skipped must not leave demo's defaults in the environment
        os.environ.pop(_applied_rt.pop(), None)


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
        summary = seed_demo.seed(governance=True, **kw)
    from . import demo_connections
    demo_connections.seed()
    return summary


class DemoGuardError(Exception):
    """Raised when demo seeding/reset is refused because the DB is not demo-owned."""


SHARED_DB = Path("/mnt/data/ai-control-tower/act/act.db")


def _marker_present(cx) -> bool:
    from sqlalchemy import text
    return cx.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='act_demo_marker'")).first() is not None \
        and cx.execute(text("SELECT 1 FROM act_demo_marker")).first() is not None


def _write_marker(eng=None) -> None:
    from sqlalchemy import text
    from . import db
    with (eng or db.engine).begin() as cx:
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
        if marked:  # a demo DB seeded before the connection samples existed gets them once
            from . import demo_connections
            demo_connections.seed()
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
    """Wipe the demo DB and reseed it. Only a demo-owned DB (marker present) is ever wiped.
    Runs under _rt_lock, so a switch-off cannot swap the engine mid-way; the engine is captured once."""
    from . import db, demo_governance
    with _rt_lock:
        if not enabled():
            _refuse("refusing to reset: demo mode is off")
        eng = db.engine
        if eng is db.real_engine and not env_enabled():
            _refuse("refusing to reset: the active database is the real database")
        _check_not_shared()
        with eng.connect() as cx:
            if not _marker_present(cx):
                _refuse(f"refusing to reset {db.DB_PATH}: it is not a demo-owned database (no demo marker)")
        with demo_governance.lock:  # app_settings is kept: when ACT_DEMO_MODE is in the environment it shares this DB
            db.metadata.drop_all(eng, tables=[t for t in db.metadata.sorted_tables if t.name != "app_settings"])
            demo_governance.clear()
        db.init_db(eng)
        from . import demo_connections
        demo_connections.clear(eng)  # connections, scans and staged rows never survive a reset
        _write_marker(eng)
        if db.engine is not eng:  # cannot happen under _rt_lock; never seed another DB if it does
            _refuse("refusing to seed: the active database changed during the reset")
        return _seed()
