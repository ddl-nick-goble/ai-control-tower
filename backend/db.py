"""SQLite storage for ACT (single DB, pooled connections; WAL on local disk, DELETE journal on network filesystems)."""
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
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

_NETWORK_FS = ("nfs", "nfs4", "cifs", "smb", "smb2", "smb3", "smbfs", "ceph", "glusterfs", "lustre", "9p",
               "fuse.sshfs", "fuse.s3fs", "fuse.gcsfuse", "fuse.goofys", "fuse.rclone", "fuse.juicefs")


def _unescape_mount(s: str) -> str:
    for esc, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        s = s.replace(esc, ch)
    return s


def _read_mounts():
    """Mount table as [(mountpoint, fstype)], or None when /proc/mounts is unreadable."""
    out = []
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    out.append((_unescape_mount(parts[1]), parts[2]))
    except OSError as e:
        log.warning("cannot read /proc/mounts (%s); cannot tell the DB filesystem type, "
                    "falling back to the safe DELETE journal mode", e)
        return None
    return out


def fs_type(path, mounts=None) -> str:
    """Filesystem type of `path`: longest-prefix match of its resolved path against the mount table."""
    if mounts is None:
        mounts = _read_mounts() or []
    p = str(Path(path).expanduser().resolve())
    best, best_len = "unknown", -1
    for mp, fst in mounts:
        mp_n = mp.rstrip("/")
        if (p == mp_n or p.startswith(mp_n + "/") or mp_n == "") and len(mp_n) >= best_len:
            best, best_len = fst, len(mp_n)
    return best


def is_network_fs(path, mounts=None) -> bool:
    t = fs_type(path, mounts).lower()
    return t in _NETWORK_FS


def journal_mode_for(path, mounts=None) -> str:
    override = os.environ.get("ACT_DB_JOURNAL_MODE")
    if override:
        if override.strip().upper() in ("WAL", "DELETE"):
            return override.strip().upper()
        log.warning("ignoring ACT_DB_JOURNAL_MODE=%r (must be WAL or DELETE)", override)
    if mounts is None:
        mounts = _read_mounts()
        if mounts is None:  # unreadable mount table: the safe mode, not WAL
            return "DELETE"
    return "DELETE" if is_network_fs(path, mounts) else "WAL"


def pool_size_for(path, mounts=None) -> int:
    override = os.environ.get("ACT_DB_POOL_SIZE")
    if override:
        try:
            if int(override) > 0:
                return int(override)
        except ValueError:
            pass
        log.warning("ignoring ACT_DB_POOL_SIZE=%r (must be a positive integer)", override)
    return 4 if is_network_fs(path, mounts) else 20


class DatabaseOpenError(Exception):
    pass


def _lock_wait() -> float:
    try:
        return float(os.environ.get("ACT_DB_LOCK_WAIT", "") or 150)
    except ValueError:
        return 150.0


def _header_is_wal(path) -> bool:
    try:
        with open(path, "rb") as f:
            h = f.read(100)
    except OSError:
        return False
    return len(h) >= 20 and h[18] == 2 and h[19] == 2


def _stale_aside(path) -> bool:
    """Rename a 0-byte -wal (and its -shm) aside. A non-empty -wal is never touched. True if renamed."""
    wal = Path(f"{path}-wal")
    try:
        if not wal.exists() or wal.stat().st_size != 0:
            return False
    except OSError:
        return False
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for suffix in ("-wal", "-shm"):
        src = Path(f"{path}{suffix}")
        if src.exists():
            dst = Path(f"{path}{suffix}.stale-{ts}")
            try:
                src.rename(dst)
                log.warning("moved stale %s aside to %s (0-byte -wal left by a crash)", src, dst)
            except OSError as e:
                log.warning("could not move stale %s aside: %s", src, e)
    return True


def check_open(path) -> None:
    """Open the DB the way the app will; retry through lock contention (NFSv4 lease), then raise one clear
    DatabaseOpenError."""
    mode = journal_mode_for(path)
    wait = _lock_wait()
    start = time.monotonic()
    delay, attempt = 1.0, 0
    while True:
        attempt += 1
        try:
            if mode == "DELETE" and is_network_fs(path) and _header_is_wal(path):
                log.info("database %s is in WAL mode on a network filesystem; converting to DELETE journal mode",
                         path)
            conn = sqlite3.connect(str(path), timeout=30)
            try:
                conn.execute(f"PRAGMA journal_mode={mode}").fetchall()
                conn.execute("SELECT count(*) FROM sqlite_master").fetchall()
            finally:
                conn.close()
            return
        except sqlite3.Error as e:
            locked = "locked" in str(e).lower()
            wal = Path(f"{path}-wal")
            nonempty_wal = wal.exists() and wal.stat().st_size > 0
            if locked and not nonempty_wal and _stale_aside(path):
                log.warning("database %s was locked (attempt %d): %s; stale -wal/-shm moved aside, retrying",
                            path, attempt, e)
                continue
            elapsed = time.monotonic() - start
            if not locked or elapsed >= wait:
                hint = ("Stale -wal/-shm files left by a crash are the usual cause on a network filesystem. "
                        + ("A non-empty -wal is present and holds data, so it was not touched: restore a "
                           "known-good copy with scripts/restore_db.py, and restart."
                           if nonempty_wal else
                           "Move them aside, or restore a known-good copy with scripts/restore_db.py, and restart."))
                raise DatabaseOpenError(
                    f"Cannot open database {path} (filesystem: {fs_type(path)}, journal mode: {mode}): {e} "
                    f"(after {elapsed:.0f}s, {attempt} attempt(s)). {hint}") from e
            log.warning("database %s is locked (attempt %d, %.0fs of %.0fs): %s; retrying in %.0fs",
                        path, attempt, elapsed, wait, e, delay)
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


def _make_engine(path: str):
    mode = journal_mode_for(path)
    eng = create_engine(
        f"sqlite:///{path}",
        poolclass=QueuePool,
        pool_size=pool_size_for(path),
        max_overflow=5,
        pool_pre_ping=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listen(eng, "connect", lambda c, r: _on_connect(c, r, mode))
    return eng


def _on_connect(dbapi_conn, _record, mode="WAL"):
    cur = dbapi_conn.cursor()
    cur.execute(f"PRAGMA journal_mode={mode}")
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
    Column("connection_id", String),  # PO-1: the provider connection that discovered it (set by PO-4 intake)
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

# PO-1: provider connections ("Connect a provider"). state: draft, waiting, trusted, verified, partial, connected,
# needs_attention, failed, disconnected. `config` holds what the handshake or paste learned (role ARN, project, ...);
# `scopes` is {"selected": [ids]} once the user saved a selection (None = everything selected).
connections = Table(
    "connections", metadata,
    Column("id", String, primary_key=True),
    Column("provider", String, nullable=False),
    Column("state", String, nullable=False, default="draft"),
    Column("sample", Boolean, default=False),
    Column("state_token", String, index=True),
    Column("token_used", Boolean, default=False),
    Column("config", JSON),
    Column("scopes", JSON),
    Column("created_by", String),
    Column("created_by_username", String),
    Column("created_at", String),
    Column("updated_at", String),
    Column("waiting_since", Float),
    Column("last_check", String),
    Column("last_scan", String),
    Column("disconnected_at", String),
    Column("schedule", String),         # PO-4.4: off | daily
    Column("schedule_time", String),    # HH:MM (UTC), default 02:00
    Column("schedule_job_id", String),  # the scheduled Domino Job (live connections)
)

# PO-4: one row per scan (kind scan = user started, sync = scheduled). progress is [{scope, found, errors, state}].
scans = Table(
    "scans", metadata,
    Column("id", String, primary_key=True),
    Column("connection_id", String, index=True),
    Column("kind", String, default="scan"),
    Column("state", String, default="queued"),  # queued | running | done | partial | failed
    Column("sample", Boolean, default=False),
    Column("job_id", String),
    Column("job_url", String),
    Column("started_at", String),
    Column("finished_at", String),
    Column("updated_at", String),
    Column("progress", JSON),
    Column("counts", JSON),
    Column("error", Text),
    Column("created_by", String),
    Column("created_by_username", String),
)

# PO-4: what a scan found, classed new | changed | unchanged | missing, waiting for review/import.
staged_agents = Table(
    "staged_agents", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scan_id", String, index=True),
    Column("connection_id", String, index=True),
    Column("platform", String),
    Column("native_id", String),
    Column("name", String),
    Column("class", String),
    Column("diff", JSON),
    Column("suggestion", JSON),   # base PO-2 suggestion {suggestions, tier}; overrides are applied on top
    Column("overrides", JSON),
    Column("owner", String),
    Column("flags", JSON),
    Column("sample", Boolean, default=False),
    Column("awaiting", Boolean, default=False),  # new agent from a scheduled sync, never auto-imported
    Column("record", JSON),                      # the normalized Agent Record to import
    Column("imported_agent_id", String),
)

# One row per access check, newest last (the sample tenant turns green on the second check).
connection_checks = Table(
    "connection_checks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("connection_id", String, index=True),
    Column("ts", String),
    Column("state", String),
    Column("permissions", JSON),
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
    if eng.url.database:
        check_open(eng.url.database)
    try:
        metadata.create_all(eng)
    except Exception as e:
        if "index" not in str(e).lower():
            raise
        log.warning("create_all failed (possibly the findings unique index); continuing", exc_info=True)
    _migrate(eng)
    _ensure_findings_index(eng)


def prewarm(n: int) -> None:
    conns = [engine.raw_connection() for _ in range(max(0, min(n, engine.pool.size())))]
    for c in conns:
        c.close()
