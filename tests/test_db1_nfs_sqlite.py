"""DB-1: SQLite on a network filesystem. Written from the ACs only (DB1-1, 1-3, 1-4, 1-5, 1-6).

Nothing here touches /mnt/data: every DB is under tmp_path; a network FS is simulated by patching
backend.db._read_mounts (the mount table reader), and a lock by making sqlite3.connect raise
OperationalError("database is locked"). time.sleep / time.monotonic / time.time are replaced by a fake clock so the
150 s wait runs instantly. Assumed names: db.check_open(path), db.init_db(eng), db.DatabaseOpenError,
demo.default_db_path(), demo.DATASET_DIR, demo.apply_defaults(), discovery.run_scan(sid, eng), discovery.set_scan.
"""
import importlib
import logging
import sqlite3
import sys
import tempfile
import time

import pytest
from sqlalchemy import insert, select

NFS = [("/", "nfs4")]
LOCAL = [("/", "ext4")]


def _purge():
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ACT_DB", str(tmp_path / "act.db"))
    monkeypatch.setenv("ACT_DB_PREWARM_COUNT", "0")
    monkeypatch.setenv("ACT_WORKSPACE_OWNER_MODE", "0")
    for k in ("ACT_DB_LOCK_WAIT", "ACT_DB_JOURNAL_MODE", "ACT_DEMO_DB_PATH", "ACT_DEMO_MODE",
              "DOMINO_IS_GIT_BASED", "DOMINO_PROJECT_NAME", "DOMINO_API_PROXY"):
        monkeypatch.delenv(k, raising=False)
    _purge()
    mod = importlib.import_module("backend.db")
    yield mod
    _purge()


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += max(s, 0)

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(time, "sleep", c.sleep)
    monkeypatch.setattr(time, "monotonic", c)
    monkeypatch.setattr(time, "time", c)
    return c


def _locked_connect(monkeypatch, fail_times):
    """sqlite3.connect raises 'database is locked' fail_times times (None = always), then works."""
    real = sqlite3.connect
    state = {"calls": 0}

    def connect(*a, **k):
        state["calls"] += 1
        if fail_times is None or state["calls"] <= fail_times:
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", connect)
    return state


def _make_db(path, mode):
    c = sqlite3.connect(str(path))
    c.execute(f"PRAGMA journal_mode={mode}")
    c.execute("CREATE TABLE t (x INTEGER)")
    c.execute("INSERT INTO t VALUES (7)")
    c.commit()
    c.close()


def _header(path):
    with open(path, "rb") as f:
        h = f.read(100)
    return h[18], h[19]


# ---------------------------------------------------------------- DB1-1

@pytest.fixture
def demo_mod(tmp_path, monkeypatch, db):
    monkeypatch.setenv("ACT_DEMO_MODE", "1")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    demo = importlib.import_module("backend.demo")
    ds = tmp_path / "dataset"
    ds.mkdir()
    monkeypatch.setattr(demo, "DATASET_DIR", ds)
    return demo, ds


def test_ac_db1_1_demo_db_on_pod_local_disk_when_dataset_is_network_fs(demo_mod, monkeypatch):
    demo, ds = demo_mod
    importlib.import_module("backend.db")
    monkeypatch.setattr(sys.modules["backend.db"], "_read_mounts", lambda: NFS)
    expected = str(__import__("pathlib").Path(tempfile.gettempdir()) / "act-demo" / "demo.db")
    assert demo.default_db_path() == expected


def test_ac_db1_1_demo_db_stays_in_dataset_dir_on_local_fs(demo_mod, monkeypatch):
    demo, ds = demo_mod
    monkeypatch.setattr(sys.modules["backend.db"], "_read_mounts", lambda: LOCAL)
    assert demo.default_db_path() == str(ds / "demo.db")


def test_ac_db1_1_env_override_wins(demo_mod, monkeypatch, tmp_path):
    demo, ds = demo_mod
    monkeypatch.setattr(sys.modules["backend.db"], "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DEMO_DB_PATH", str(tmp_path / "x" / "mine.db"))
    assert demo.default_db_path() == str(tmp_path / "x" / "mine.db")


def test_ac_db1_1_demo_reseeds_on_first_use_of_pod_local_db(demo_mod, monkeypatch):
    demo, ds = demo_mod
    monkeypatch.setattr(sys.modules["backend.db"], "_read_mounts", lambda: NFS)
    monkeypatch.delenv("ACT_DB", raising=False)
    demo.apply_defaults()
    import os
    from pathlib import Path
    assert os.environ["ACT_DB"] == str(Path(tempfile.gettempdir()) / "act-demo" / "demo.db")
    assert not Path(os.environ["ACT_DB"]).exists()
    assert demo._db_new is True  # first use on this pod: the demo DB is new, so it is seeded


# ---------------------------------------------------------------- DB1-3

def test_ac_db1_3_lock_that_clears_mid_wait_succeeds(db, clock, monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "20")
    state = _locked_connect(monkeypatch, 3)
    caplog.set_level(logging.INFO)
    db.check_open(tmp_path / "x.db")  # must not raise
    assert state["calls"] >= 4
    assert len(clock.sleeps) >= 3 and all(s > 0 for s in clock.sleeps)
    attempts = [r for r in caplog.records if "locked" in r.getMessage().lower()]
    assert len(attempts) >= 3  # each attempt logged with the reason


def test_ac_db1_3_backoff_grows(db, clock, monkeypatch, tmp_path):
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "60")
    _locked_connect(monkeypatch, None)
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(tmp_path / "x.db")
    assert clock.sleeps[-1] > clock.sleeps[0]


def test_ac_db1_3_gives_up_after_env_wait_then_raises(db, clock, monkeypatch, tmp_path):
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect(monkeypatch, None)
    start = clock.now
    with pytest.raises(db.DatabaseOpenError) as ei:
        db.check_open(tmp_path / "x.db")
    waited = clock.now - start
    assert 5 <= waited <= 5 + 35, waited
    assert "locked" in str(ei.value).lower()


def test_ac_db1_3_default_wait_is_150s(db, clock, monkeypatch, tmp_path):
    _locked_connect(monkeypatch, None)
    start = clock.now
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(tmp_path / "x.db")
    assert 150 <= clock.now - start <= 150 + 60


def test_ac_db1_3_init_db_retries_through_a_lock(db, clock, monkeypatch, tmp_path):
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "20")
    _locked_connect(monkeypatch, 2)
    db.init_db()
    assert clock.sleeps
    with db.engine.connect() as cx:
        assert cx.execute(select(db.agents.c.id)).all() == []


# ---------------------------------------------------------------- DB1-4

def test_ac_db1_4_zero_byte_wal_is_renamed_with_warning(db, clock, monkeypatch, caplog, tmp_path):
    p = tmp_path / "act.db"
    wal, shm = tmp_path / "act.db-wal", tmp_path / "act.db-shm"
    # WAL header on a (mocked) network FS, 0-byte -wal, exclusive lock fails
    _make_db(p, "WAL")
    wal.write_bytes(b"")
    shm.write_bytes(b"\0" * 32768)
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "20")
    _locked_connect(monkeypatch, 1)
    caplog.set_level(logging.INFO)
    db.check_open(p)
    assert not wal.exists() and not shm.exists()
    assert len(list(tmp_path.glob("act.db-wal.stale-*"))) == 1
    assert len(list(tmp_path.glob("act.db-shm.stale-*"))) == 1
    assert any(r.levelno == logging.WARNING and "stale" in r.getMessage().lower() for r in caplog.records)


def test_ac_db1_4_non_empty_wal_is_never_touched(db, clock, monkeypatch, tmp_path):
    p = tmp_path / "act.db"
    _make_db(p, "DELETE")
    wal, shm = tmp_path / "act.db-wal", tmp_path / "act.db-shm"
    wal.write_bytes(b"w" * 4152)
    shm.write_bytes(b"\0" * 32768)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect(monkeypatch, None)
    with pytest.raises(db.DatabaseOpenError) as ei:
        db.check_open(p)
    msg = str(ei.value)
    assert "scripts/restore_db.py" in msg and "-wal" in msg
    assert wal.read_bytes() == b"w" * 4152 and shm.exists()
    assert not list(tmp_path.glob("*.stale-*"))


# ---------------------------------------------------------------- DB1-5

def test_ac_db1_5_wal_header_on_network_fs_converted_to_delete(db, monkeypatch, caplog, tmp_path):
    p = tmp_path / "act.db"
    _make_db(p, "WAL")
    assert _header(p) == (2, 2)
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    caplog.set_level(logging.INFO)
    db.check_open(p)
    assert _header(p) == (1, 1)
    assert not (tmp_path / "act.db-wal").exists() and not (tmp_path / "act.db-shm").exists()
    c = sqlite3.connect(str(p))
    assert c.execute("SELECT x FROM t").fetchall() == [(7,)]
    c.close()
    assert any("wal" in r.getMessage().lower() and "delete" in r.getMessage().lower() for r in caplog.records)


def test_ac_db1_5_conversion_happens_once(db, monkeypatch, caplog, tmp_path):
    p = tmp_path / "act.db"
    _make_db(p, "WAL")
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    db.check_open(p)
    caplog.clear()
    caplog.set_level(logging.INFO)
    db.check_open(p)
    assert _header(p) == (1, 1)
    assert not any("wal" in r.getMessage().lower() and "delete" in r.getMessage().lower() for r in caplog.records)


def test_ac_db1_5_local_fs_wal_is_left_alone(db, monkeypatch, tmp_path):
    p = tmp_path / "act.db"
    _make_db(p, "WAL")
    monkeypatch.setattr(db, "_read_mounts", lambda: LOCAL)
    db.check_open(p)
    assert _header(p) == (2, 2)


# ---------------------------------------------------------------- DB1-6

def test_ac_db1_6_scan_write_hitting_lock_marks_scan_failed(db, clock, monkeypatch):
    db.init_db()
    disc = importlib.import_module("backend.discovery")
    with db.engine.begin() as cx:
        cx.execute(insert(db.connections).values(id="c1", provider="aws", state="connected", sample=True,
                                                 config={}, scopes=[]))
    sid = disc.create_scan("c1", None, "scan", True)
    real = disc.set_scan
    calls = {"n": 0}

    def flaky(sid_, eng=None, **vals):
        calls["n"] += 1
        if calls["n"] <= 2:  # the first progress write and the first attempt to record the failure
            raise sqlite3.OperationalError("database is locked")
        return real(sid_, eng, **vals)
    monkeypatch.setattr(disc, "set_scan", flaky)
    disc.run_scan(sid, db.engine)  # must not raise
    with db.engine.connect() as cx:
        row = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
    assert row.state == "failed"
    assert row.error and "locked" in row.error.lower()


# ================================================================ review-gap tests (tightened spec)

import datetime as _dt
import os
from pathlib import Path

from sqlalchemy.exc import OperationalError as SAOperationalError

BUSY = 30  # the per-attempt connect timeout the app uses


def _locked_connect_timed(monkeypatch, clock, max_calls=300):
    """Always 'locked'; each attempt costs its connect timeout on the fake clock (as sqlite's busy wait would).
    Aborts with RuntimeError after max_calls so a retry loop that never ends fails instead of hanging."""
    state = {"calls": 0}

    def connect(*a, **k):
        state["calls"] += 1
        if state["calls"] > max_calls:
            raise RuntimeError("check_open kept retrying forever")
        clock.now += k.get("timeout", a[1] if len(a) > 1 else 5.0)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(sqlite3, "connect", connect)
    return state


def _stale_setup(tmp_path, wal_bytes=b"", shm=b"\0" * 32768, mode="WAL"):
    p = tmp_path / "act.db"
    if p.exists():
        c = sqlite3.connect(str(p))
        c.execute(f"PRAGMA journal_mode={mode}").fetchall()
        c.close()
    else:
        _make_db(p, mode)
    for suf in ("-wal", "-shm"):
        (tmp_path / f"act.db{suf}").unlink(missing_ok=True)
    (tmp_path / "act.db-wal").write_bytes(wal_bytes)
    (tmp_path / "act.db-shm").write_bytes(shm)
    return p


# ---------------------------------------------------------------- DB1-3 bound

@pytest.mark.parametrize("n", [5, 20, 45, 100, 150])
def test_ac_db1_3_bound_total_time_within_wait_plus_one_attempt(db, clock, monkeypatch, tmp_path, n):
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", str(n))
    _locked_connect_timed(monkeypatch, clock)
    start = clock.now
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(tmp_path / "x.db")
    assert clock.now - start <= n + BUSY, (clock.now - start, clock.sleeps)


@pytest.mark.parametrize("n", [5, 20, 150])
def test_ac_db1_3_bound_sleeps_total_not_beyond_wait(db, clock, monkeypatch, tmp_path, n):
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", str(n))
    _locked_connect(monkeypatch, None)  # attempts cost no time: only the sleeps count
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(tmp_path / "x.db")
    assert sum(clock.sleeps) <= n, clock.sleeps


def test_ac_db1_3_rename_failure_raises_after_bounded_attempts(db, clock, monkeypatch, tmp_path):
    p = _stale_setup(tmp_path)
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    state = _locked_connect_timed(monkeypatch, clock)

    def boom(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(Path, "rename", boom)
    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(p)
    assert state["calls"] <= 20, state["calls"]


# ---------------------------------------------------------------- DB1-4 guard

def _assert_untouched(tmp_path, shm=b"\0" * 32768):
    assert (tmp_path / "act.db-wal").exists() and (tmp_path / "act.db-shm").exists()
    assert (tmp_path / "act.db-shm").read_bytes() == shm
    assert not list(tmp_path.glob("*.stale-*"))


def test_ac_db1_4_guard_delete_header_on_network_fs_not_renamed(db, clock, monkeypatch, tmp_path):
    p = _stale_setup(tmp_path, mode="DELETE")
    assert _header(p) == (1, 1)
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect_timed(monkeypatch, clock)
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(p)
    _assert_untouched(tmp_path)


def test_ac_db1_4_guard_wal_header_on_local_fs_not_renamed(db, clock, monkeypatch, tmp_path):
    p = _stale_setup(tmp_path, mode="WAL")
    assert _header(p) == (2, 2)
    monkeypatch.setattr(db, "_read_mounts", lambda: LOCAL)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect_timed(monkeypatch, clock)
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(p)
    _assert_untouched(tmp_path)


def test_ac_db1_4_guard_live_second_connection_files_not_renamed(db, monkeypatch, tmp_path):
    p = tmp_path / "act.db"
    live = sqlite3.connect(str(p), isolation_level=None)
    assert live.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    live.execute("CREATE TABLE t (x INTEGER)")
    live.execute("INSERT INTO t VALUES (1)")
    live.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    live.execute("BEGIN IMMEDIATE")
    wal, shm = tmp_path / "act.db-wal", tmp_path / "act.db-shm"
    assert wal.exists() and wal.stat().st_size == 0 and shm.exists()
    ino = (wal.stat().st_ino, shm.stat().st_ino)

    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "1")
    real = sqlite3.connect
    calls = {"n": 0}

    def connect(*a, **k):
        calls["n"] += 1
        if calls["n"] > 50:
            raise RuntimeError("check_open kept retrying forever")
        return real(a[0], **{**k, "timeout": 0.05})
    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        try:
            db.check_open(p)
        except db.DatabaseOpenError:
            pass
        assert wal.exists() and shm.exists()
        assert (wal.stat().st_ino, shm.stat().st_ino) == ino
        assert not list(tmp_path.glob("*.stale-*"))
        live.execute("INSERT INTO t VALUES (2)")
        live.execute("COMMIT")
    finally:
        live.close()
    c = real(str(p))
    assert sorted(r[0] for r in c.execute("SELECT x FROM t")) == [1, 2]
    c.close()


def test_ac_db1_4_stale_rename_never_overwrites_earlier_stale_file(db, clock, monkeypatch, tmp_path):
    fixed = _dt.datetime(2030, 1, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)

    class Frozen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(db, "datetime", Frozen, raising=False)
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "20")
    for marker in (b"A" * 64, b"B" * 64):
        p = _stale_setup(tmp_path, shm=marker)
        _locked_connect(monkeypatch, 1)
        db.check_open(p)
    shms = sorted(tmp_path.glob("act.db-shm.stale-*"))
    wals = sorted(tmp_path.glob("act.db-wal.stale-*"))
    assert len(shms) == 2 and len(wals) == 2, (shms, wals)
    assert sorted(f.read_bytes() for f in shms) == [b"A" * 64, b"B" * 64]


# ---------------------------------------------------------------- DB1-5 once

def _conversion_logs(caplog):
    return [r for r in caplog.records if "convert" in r.getMessage().lower()]


def test_ac_db1_5_conversion_log_once_despite_retries(db, clock, monkeypatch, caplog, tmp_path):
    p = tmp_path / "act.db"
    _make_db(p, "WAL")
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "60")
    _locked_connect(monkeypatch, 3)
    caplog.set_level(logging.INFO)
    db.check_open(p)
    assert _header(p) == (1, 1)
    assert len(_conversion_logs(caplog)) == 1, [r.getMessage() for r in _conversion_logs(caplog)]


def test_ac_db1_5_race_header_already_delete_does_no_conversion(db, clock, monkeypatch, caplog, tmp_path):
    p = tmp_path / "act.db"
    _make_db(p, "WAL")
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "60")
    real = sqlite3.connect
    state = {"n": 0}

    def connect(*a, **k):
        state["n"] += 1
        if state["n"] == 1:  # someone else converts while we are locked out
            c = real(str(p))
            c.execute("PRAGMA journal_mode=DELETE").fetchall()
            c.close()
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)
    monkeypatch.setattr(sqlite3, "connect", connect)
    caplog.set_level(logging.INFO)
    db.check_open(p)
    assert _header(p) == (1, 1)
    assert _conversion_logs(caplog) == []


# ---------------------------------------------------------------- unhandled error

def test_ac_db1_4_wal_vanishing_between_exists_and_stat_is_not_a_raw_oserror(db, clock, monkeypatch, tmp_path):
    p = _stale_setup(tmp_path)
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect_timed(monkeypatch, clock)
    real_exists, real_stat = Path.exists, Path.stat

    def exists(self, *a, **k):
        return True if str(self).endswith("-wal") else real_exists(self, *a, **k)

    def stat(self, *a, **k):
        if str(self).endswith("-wal"):
            raise FileNotFoundError(str(self))
        return real_stat(self, *a, **k)
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(Path, "stat", stat)
    try:
        db.check_open(p)
    except db.DatabaseOpenError:
        pass  # allowed; a raw OSError is not


# ---------------------------------------------------------------- DB1-6 message

def test_ac_db1_6_failure_reason_says_locked_and_has_no_sql(db, clock, monkeypatch):
    db.init_db()
    disc = importlib.import_module("backend.discovery")
    with db.engine.begin() as cx:
        cx.execute(insert(db.connections).values(id="c1", provider="aws", state="connected", sample=True,
                                                 config={}, scopes=[]))
    sid = disc.create_scan("c1", None, "scan", True)
    real = disc.set_scan
    calls = {"n": 0}

    def flaky(sid_, eng=None, **vals):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise SAOperationalError("UPDATE scans SET progress=? WHERE scans.id = ?", {"id": sid_},
                                     sqlite3.OperationalError("database is locked"))
        return real(sid_, eng, **vals)
    monkeypatch.setattr(disc, "set_scan", flaky)
    disc.run_scan(sid, db.engine)
    with db.engine.connect() as cx:
        row = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
    assert row.state == "failed"
    assert row.error and "locked" in row.error.lower()
    for bad in ("INSERT", "UPDATE", "SELECT", "[parameters:", "[SQL"):
        assert bad.lower() not in row.error.lower(), row.error
