"""SQLite storage for ACT (single DB, WAL, pooled connections)."""
import logging
import os
import threading
from pathlib import Path

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer,
                        MetaData, String, Table, Text, create_engine, event, inspect, text)
from sqlalchemy.pool import QueuePool


def _db_path() -> str:
    if os.environ.get("ACT_DB"):
        return os.environ["ACT_DB"]
    project = os.environ.get("DOMINO_PROJECT_NAME")
    if project and os.environ.get("DOMINO_IS_GIT_BASED", "").lower() == "true":
        return f"/mnt/data/{project}/act/act.db"
    if project and os.environ.get("DOMINO_API_PROXY"):
        return f"/domino/datasets/local/{project}/act/act.db"
    return "./act.db"


log = logging.getLogger(__name__)
DB_PATH = _db_path()
try:
    Path(DB_PATH).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
except OSError:
    log.exception("could not create DB directory for %s", DB_PATH)

def _make_engine(path: str):
    eng = create_engine(
        f"sqlite:///{path}",
        poolclass=QueuePool,
        pool_size=20,
        max_overflow=5,
        pool_pre_ping=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listen(eng, "connect", _on_connect)
    return eng


def _on_connect(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


engine = _make_engine(DB_PATH)
# The "real" DB (settings, audit of settings changes, key file) never changes; `engine` is the active DB
# and is swapped to a demo DB by the runtime demo switch (use_path / use_real).
real_engine = engine
REAL_DB_PATH = DB_PATH


_engines_lock = threading.Lock()
_engines: dict = {}  # demo engines by resolved path: kept for reuse, never disposed while requests may hold them


def engine_for(path: str):
    """The (cached) engine for `path`; one engine per path for the life of the process."""
    key = str(Path(path).expanduser().resolve())
    with _engines_lock:
        if key not in _engines:
            Path(key).parent.mkdir(parents=True, exist_ok=True)
            _engines[key] = _make_engine(key)
        return _engines[key]


def swap(new_engine, new_path: str):
    """Make `new_engine` the active DB. Returns the previous (engine, path) so a failed switch can restore it."""
    global engine, DB_PATH
    prev = (engine, DB_PATH)
    engine, DB_PATH = new_engine, new_path
    return prev


def use_path(path: str) -> None:
    swap(engine_for(path), path)


def use_real() -> None:
    swap(real_engine, REAL_DB_PATH)


metadata = MetaData()

agents = Table(
    "agents", metadata,
    Column("id", String, primary_key=True),  # act:<platform>:<hash16>
    Column("source_platform", String, nullable=False),
    Column("native_id", String, nullable=False),
    Column("name", String),
    Column("owner", String),
    Column("tier", String),
    Column("tier_reasoning", Text),
    Column("criteria", JSON),
    Column("status", String, default="discovered"),
    Column("registered_by", String),
    Column("registered_by_username", String),
    Column("last_seen", String),
    Column("record", JSON),
    Column("bundle_id", String),
    Column("governance", JSON),
)

agent_versions = Table(
    "agent_versions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), nullable=False, index=True),
    Column("version", String),
    Column("created_at", String),
    Column("record", JSON),
)

manifests = Table(
    "manifests", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), nullable=False, index=True),
    Column("version", String),
    Column("content", JSON),
    Column("created_at", String),
)

identities = Table(
    "identities", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), nullable=False, index=True),
    Column("kind", String),
    Column("principal", String),
    Column("scopes", JSON),
    Column("record", JSON),
)

dependencies = Table(
    "dependencies", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), nullable=False, index=True),
    Column("kind", String),
    Column("name", String),
    Column("target", String),
    Column("record", JSON),
)

runs = Table(
    "runs", metadata,
    Column("id", String, primary_key=True),
    Column("agent_id", String, ForeignKey("agents.id"), index=True),
    Column("status", String),
    Column("started_at", String),
    Column("ended_at", String),
    Column("cost", Float),
    Column("record", JSON),
)

events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, index=True),  # no FK: shadow-agent spans have no run row
    Column("agent_id", String, index=True),
    Column("ts", String),
    Column("kind", String),
    Column("span_id", String),  # nullable; unique with run_id for idempotent ingest
    Column("parent_span_id", String),  # additive: span tree for ancestor agent inheritance
    Column("agent_source", String),  # additive: explicit | ancestor | run_fallback | unknown
    Column("raw_span", JSON),
    Column("event", JSON),
    Column("prev_hash", String),
    Column("hash", String, unique=True),
    Index("ux_events_run_span", "run_id", "span_id", unique=True),
    Index("ix_events_run_agent", "run_id", "agent_id"),
)

findings = Table(
    "findings", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), index=True),
    Column("run_id", String),
    Column("severity", String),
    Column("rule", String),
    Column("message", Text),
    Column("status", String, default="open"),
    Column("created_at", String),
    Column("detail", JSON),
    Column("type", String),
    Column("tool", String),
    Column("span_id", String),
    Column("ts", String),
    Column("reason", Text),
    Column("observed_value", String),
    Column("limit", String),
    Index("ux_findings_run_drift", "run_id", "type", unique=True,
          sqlite_where=text("type = 'scope_drift'"), postgresql_where=text("type = 'scope_drift'")),
)

approvals = Table(
    "approvals", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), nullable=False, index=True),
    Column("approver", String, nullable=False),
    Column("decision", String),
    Column("created_at", String),
    Column("expires_at", String),
    Column("note", Text),
    Column("run_id", String),
    Column("governance_sync", String),
)

overrides = Table(
    "overrides", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, index=True),
    Column("agent_id", String, index=True),
    Column("actor_user_id", String),
    Column("actor_username", String),
    Column("reason", Text),
    Column("ts", String),
)

validation_records = Table(
    "validation_records", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), index=True),
    Column("kind", String),
    Column("result", String),
    Column("created_at", String),
    Column("detail", JSON),
)

evidence_packs = Table(
    "evidence_packs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", String, ForeignKey("agents.id"), index=True),
    Column("created_at", String),
    Column("created_by", String),
    Column("path", String),
    Column("content", JSON),
)

audit_events = Table(
    "audit_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_user_id", String, index=True),
    Column("actor_username", String),
    Column("action", String, nullable=False),
    Column("target", String),
    Column("ts", String),
    Column("detail", JSON),
)

# In-app Settings (T-39). Secret values are Fernet ciphertext; plain values are text. Always in the real DB.
app_settings = Table(
    "app_settings", metadata,
    Column("name", String, primary_key=True),
    Column("value", Text),
    Column("is_secret", Boolean, default=False),
    Column("changed_by", String),
    Column("changed_at", String),
)


def _migrate(engine) -> None:
    """Idempotent additive migration: add any declared column missing from an existing table."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or col.primary_key:
                    continue
                if not col.nullable and col.default is None and col.server_default is None:
                    log.warning("skipping migration of NOT NULL column %s.%s without default",
                                table.name, col.name)
                    continue
                ctype = col.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ctype}'))
        if insp.has_table("events"):
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_events_run_span "
                              "ON events (run_id, span_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_events_run_agent "
                              "ON events (run_id, agent_id)"))


def _ensure_findings_index(engine) -> None:
    """Unique partial index for one scope_drift finding per run; never crash on legacy duplicates."""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_findings_run_drift "
                              "ON findings (run_id, type) WHERE type = 'scope_drift'"))
    except Exception:
        log.warning("could not create ux_findings_run_drift (existing duplicate scope_drift findings?)",
                    exc_info=True)


def init_db(eng=None) -> None:
    eng = eng or engine
    try:
        metadata.create_all(eng)
    except Exception as e:
        if "index" not in str(e).lower():
            raise
        log.warning("create_all failed (possibly the findings unique index); continuing", exc_info=True)
    _migrate(eng)
    _ensure_findings_index(eng)


def prewarm(n: int) -> None:
    conns = [engine.raw_connection() for _ in range(max(0, min(n, 20)))]
    for c in conns:
        c.close()
