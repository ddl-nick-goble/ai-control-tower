"""SQLite storage for ACT (single DB, WAL, pooled connections)."""
import logging
import os
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

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=5,
    pool_pre_ping=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _on_connect(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


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
    Column("last_seen", String),
    Column("record", JSON),
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
    Column("raw_span", JSON),
    Column("event", JSON),
    Column("prev_hash", String),
    Column("hash", String, unique=True),
    Index("ux_events_run_span", "run_id", "span_id", unique=True),
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


def _migrate() -> None:
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


def init_db() -> None:
    metadata.create_all(engine)
    _migrate()


def prewarm(n: int) -> None:
    conns = [engine.raw_connection() for _ in range(max(0, min(n, 20)))]
    for c in conns:
        c.close()
