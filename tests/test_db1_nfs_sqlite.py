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
    _make_db(p, "DELETE")
    wal, shm = tmp_path / "act.db-wal", tmp_path / "act.db-shm"
    wal.write_bytes(b"")
    shm.write_bytes(b"\0" * 32768)
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
