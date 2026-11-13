"""DB-1: SQLite on a network filesystem. Written from the ACs only (DB1-1, 1-3, 1-4, 1-5, 1-6, B1, B3, B4).

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
    assert clock.now - start <= n + 2 * BUSY, (clock.now - start, clock.sleeps)


@pytest.mark.parametrize("n", [5, 20, 150])
def test_ac_db1_3_bound_sleeps_total_not_beyond_wait(db, clock, monkeypatch, tmp_path, n):
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", str(n))
    _locked_connect(monkeypatch, None)  # attempts cost no time: only the sleeps count
    with pytest.raises(db.DatabaseOpenError):
        db.check_open(tmp_path / "x.db")
    assert sum(clock.sleeps) <= n, clock.sleeps


def test_ac_db1_4_live_second_connection_files_untouched_and_peer_can_commit(db, monkeypatch, tmp_path):
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



# ================================================================ round 3

# ---------------------------------------------------------------- DB1-4 (replaced): never rename, move or delete

@pytest.mark.parametrize("mounts", [NFS, LOCAL], ids=["network_fs", "local_fs"])
@pytest.mark.parametrize("mode", ["WAL", "DELETE"], ids=["wal_header", "delete_header"])
@pytest.mark.parametrize("wal_bytes", [b"", b"w" * 4152], ids=["zero_byte_wal", "non_empty_wal"])
def test_ac_db1_4_startup_never_touches_wal_shm_and_error_names_them(db, clock, monkeypatch, tmp_path,
                                                                    mounts, mode, wal_bytes):
    p = _stale_setup(tmp_path, wal_bytes=wal_bytes, mode=mode)
    wal, shm = tmp_path / "act.db-wal", tmp_path / "act.db-shm"
    before = sorted(f.name for f in tmp_path.iterdir())
    ino = (wal.stat().st_ino, shm.stat().st_ino)
    monkeypatch.setattr(db, "_read_mounts", lambda: mounts)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect_timed(monkeypatch, clock)
    with pytest.raises(db.DatabaseOpenError) as ei:
        db.check_open(p)
    assert sorted(f.name for f in tmp_path.iterdir()) == before  # nothing renamed, moved, deleted or added
    assert (wal.stat().st_ino, shm.stat().st_ino) == ino
    assert wal.read_bytes() == wal_bytes and shm.read_bytes() == b"\0" * 32768
    msg = str(ei.value)
    assert f"{p}-wal" in msg or "act.db-wal" in msg
    assert f"{p}-shm" in msg or "act.db-shm" in msg
    assert "scripts/restore_db.py" in msg


def test_ac_db1_4_error_names_only_the_leftover_files_that_exist(db, clock, monkeypatch, tmp_path):
    p = _stale_setup(tmp_path, wal_bytes=b"", mode="DELETE")
    (tmp_path / "act.db-shm").unlink()
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect_timed(monkeypatch, clock)
    with pytest.raises(db.DatabaseOpenError) as ei:
        db.check_open(p)
    msg = str(ei.value)
    assert "act.db-wal" in msg and "scripts/restore_db.py" in msg
    assert "act.db-shm" not in msg
    assert (tmp_path / "act.db-wal").exists()


def test_ac_db1_4_no_rename_code_path_is_called(db, clock, monkeypatch, tmp_path):
    p = _stale_setup(tmp_path)
    monkeypatch.setattr(db, "_read_mounts", lambda: NFS)
    monkeypatch.setenv("ACT_DB_LOCK_WAIT", "5")
    _locked_connect(monkeypatch, 1)  # one lock, then the open would succeed
    calls = []
    for owner, name in ((os, "rename"), (os, "replace"), (os, "remove"), (os, "unlink"), (Path, "rename"),
                        (Path, "replace"), (Path, "unlink")):
        real = getattr(owner, name)
        monkeypatch.setattr(owner, name, lambda *a, _r=real, _n=name, **k: (calls.append((_n, a)), _r(*a, **k))[1])
    try:
        db.check_open(p)
    except db.DatabaseOpenError:
        pass
    touched = [c for c in calls if any("act.db-" in str(x) for x in c[1])]
    assert touched == []
    assert (tmp_path / "act.db-wal").exists() and (tmp_path / "act.db-shm").exists()


# ---------------------------------------------------------------- DB1-5: log only when DELETE really took effect

def test_ac_db1_5_no_converted_log_when_peer_keeps_wal(db, monkeypatch, caplog, tmp_path):
    p = tmp_path / "act.db"
    peer = sqlite3.connect(str(p), isolation_level=None)
    assert peer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"  # PRAGMA journal_mode=DELETE now returns "wal"
    peer.execute("CREATE TABLE t (x INTEGER)")
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
    caplog.set_level(logging.INFO)
    try:
        try:
            db.check_open(p)
            ok = True
        except db.DatabaseOpenError as e:
            ok = False
            assert "locked" in str(e).lower() or "wal" in str(e).lower()
        conv = [r.getMessage() for r in caplog.records if "converted" in r.getMessage().replace(str(tmp_path), "").lower()]
        assert conv == [], conv
        assert ok or _header(p) == (2, 2)
        if ok:
            assert _header(p) == (1, 1)  # only acceptable if the conversion really happened
    finally:
        peer.close()


# ---------------------------------------------------------------- DB1-6 additions

def test_ac_db1_6_non_db_error_containing_locked_is_not_reported_as_db_lock(db, clock, monkeypatch):
    db.init_db()
    disc = importlib.import_module("backend.discovery")
    with db.engine.begin() as cx:
        cx.execute(insert(db.connections).values(id="c1", provider="aws", state="connected", sample=True,
                                                 config={}, scopes=[]))
    sid = disc.create_scan("c1", None, "scan", True)

    def boom(*a, **k):
        raise RuntimeError("resource locked by provider")
    monkeypatch.setattr(disc, "_run_scan", boom)
    writes = {"n": 0}
    real = disc.set_scan

    def counting(*a, **k):
        writes["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(disc, "set_scan", counting)
    disc.run_scan(sid, db.engine)
    with db.engine.connect() as cx:
        row = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
    assert row.state == "failed"
    err = (row.error or "").lower()
    assert "database" not in err and "could not save its progress" not in err, row.error
    assert "resource locked by provider" in err, row.error
    assert not clock.sleeps  # no lock-retry loop for a non-DB error
    assert writes["n"] == 1


def test_ac_db1_6_failure_reason_does_not_promise_a_retry(db, clock, monkeypatch):
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
            raise sqlite3.OperationalError("database is locked")
        return real(sid_, eng, **vals)
    monkeypatch.setattr(disc, "set_scan", flaky)
    disc.run_scan(sid, db.engine)
    with db.engine.connect() as cx:
        row = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
    assert row.state == "failed" and "locked" in row.error.lower()
    assert "retried" not in row.error.lower() and "will be retr" not in row.error.lower(), row.error


# ---------------------------------------------------------------- B1: demo path chosen before db.DB_PATH is fixed

def test_b1_fresh_import_in_demo_mode_never_selects_real_dataset_db(tmp_path):
    import json
    import subprocess
    root = Path(__file__).resolve().parent.parent
    script = r'''
import builtins, io, json, pathlib, sys
DS = "/mnt/data/ai-control-tower/act"
real_open = builtins.open
def fake_open(f, *a, **k):
    if f == "/proc/mounts":
        return io.StringIO("nfs:/export /mnt/data nfs4 rw 0 0\n/dev/x / ext4 rw 0 0\n")
    return real_open(f, *a, **k)
builtins.open = fake_open
P = pathlib.Path
_isdir, _resolve = P.is_dir, P.resolve
P.is_dir = lambda self, *a, **k: True if str(self) == DS else _isdir(self, *a, **k)
P.resolve = lambda self, *a, **k: self if str(self).startswith("/mnt/data/") else _resolve(self, *a, **k)
import backend
from backend import db, demo
print(json.dumps({"db_path": str(db.DB_PATH), "default": demo.default_db_path()}))
'''
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ACT_", "DOMINO_")) and k not in ("HOME", "TMPDIR")}
    home, tmpd, cwd = tmp_path / "home", tmp_path / "tmp", tmp_path / "cwd"
    for d in (home, tmpd, cwd):
        d.mkdir()
    env.update(HOME=str(home), TMPDIR=str(tmpd), ACT_DEMO_MODE="1", PYTHONPATH=str(root))
    r = subprocess.run([sys.executable, "-c", script], env=env, cwd=str(cwd), capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["default"] == str(tmpd / "act-demo" / "demo.db"), out
    assert out["db_path"] == out["default"], out
    assert "ai-control-tower" not in out["db_path"] and not out["db_path"].endswith("/act.db")


# ---------------------------------------------------------------- B3 / B4: health while the DB opens

@pytest.fixture
def app_env(db, tmp_path, monkeypatch):
    monkeypatch.setenv("ACT_DB_STARTUP_GRACE", "0.01")
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("ACT_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "owner")
    return importlib.import_module("backend.main")


def test_b3_health_while_starting_is_fast_200_with_no_db_access_and_no_path(app_env, monkeypatch, tmp_path):
    import threading
    from fastapi.testclient import TestClient
    main = app_env
    release = threading.Event()
    real_init = main.db.init_db

    def slow_init(*a, **k):
        release.wait(30)
        return real_init(*a, **k)
    monkeypatch.setattr(main.db, "init_db", slow_init)
    touched = []
    monkeypatch.setattr(main.db.real_engine, "connect", lambda *a, **k: touched.append("connect"))
    monkeypatch.setattr(main.settings, "_rows", lambda *a, **k: touched.append("_rows") or {})
    try:
        with TestClient(main.app) as c:
            t0 = time.perf_counter()
            r = c.get("/api/health")
            took = time.perf_counter() - t0
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "starting"
            assert took < 1.0, took
            assert touched == []
            text = r.text
            assert str(tmp_path) not in text and "act.db" not in text and ".db" not in text
            assert "/mnt/" not in text and "/tmp" not in text
            release.set()
    finally:
        release.set()


def test_b4_app_becomes_ready_after_open_fails_at_first_without_restart(app_env, monkeypatch):
    from fastapi.testclient import TestClient
    main = app_env
    monkeypatch.setenv("ACT_DB_RETRY_INTERVAL", "0.05")  # assumed name
    real_init = main.db.init_db
    state = {"n": 0}

    def flaky_init(*a, **k):
        state["n"] += 1
        if state["n"] <= 3:
            raise main.db.DatabaseOpenError("Cannot open database: database is locked")
        return real_init(*a, **k)
    monkeypatch.setattr(main.db, "init_db", flaky_init)
    with TestClient(main.app) as c:
        deadline = time.monotonic() + 15
        body = c.get("/api/health").json()
        while body["status"] != "ok" and time.monotonic() < deadline:
            time.sleep(0.1)
            body = c.get("/api/health").json()
        assert body["status"] == "ok" and body["db"] == "ready", body
        assert state["n"] >= 4
        assert c.get("/api/agents").status_code != 503
