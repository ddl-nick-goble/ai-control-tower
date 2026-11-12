"""T-50f: restore_db / db follow-ups (DBF-1..DBF-5). tmp_path databases only."""
import builtins
import fcntl
import glob
import importlib.util
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "restore_db.py"
SIB = ("", "-wal", "-shm", "-journal")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ACT_DB_JOURNAL_MODE", raising=False)


def _run(*args, tmp_path):
    env = dict(os.environ)
    env.pop("ACT_DB_JOURNAL_MODE", None)
    env["ACT_DB"] = str(tmp_path / "unused.db")
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, cwd=str(ROOT), env=env, timeout=120)


def _src(tmp_path):
    src = tmp_path / "src.db"
    c = sqlite3.connect(src)
    c.execute("CREATE TABLE widgets (id INTEGER)")
    c.executemany("INSERT INTO widgets VALUES (?)", [(1,), (2,), (3,)])
    c.commit()
    c.close()
    return src


def _garbage_dest(tmp_path):
    dest = tmp_path / "dest.db"
    dest.write_bytes(b"this is not a database " * 200)
    Path(str(dest) + "-wal").write_bytes(b"stale wal bytes" * 50)
    Path(str(dest) + "-shm").write_bytes(b"stale shm bytes" * 50)
    return dest


def _snapshot(dest):
    return {s: Path(str(dest) + s).read_bytes() for s in SIB if Path(str(dest) + s).exists()}


def _good_dest(tmp_path):
    dest = tmp_path / "dest.db"
    c = sqlite3.connect(dest)
    c.execute("CREATE TABLE keep (x)")
    c.execute("INSERT INTO keep VALUES (42)")
    c.commit()
    c.close()
    return dest


# ---- DBF-1
def test_dbf1_unopenable_refused_with_message_naming_files(tmp_path):
    src, dest = _src(tmp_path), _garbage_dest(tmp_path)
    before = _snapshot(dest)
    r = _run("--from", str(src), "--to", str(dest), "--apply", tmp_path=tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "--force" in out
    for s in ("", "-wal", "-shm"):
        assert str(dest) + s in out, out
    assert "locked" not in out.lower()  # not reported as busy
    assert glob.glob(str(dest) + ".broken-*") == []
    assert _snapshot(dest) == before


def test_dbf1_force_moves_unopenable_dest_and_siblings_aside(tmp_path):
    src, dest = _src(tmp_path), _garbage_dest(tmp_path)
    before = _snapshot(dest)
    r = _run("--from", str(src), "--to", str(dest), "--apply", "--force", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    for s in ("", "-wal", "-shm"):
        moved = glob.glob(str(dest) + ".broken-*" + s)
        moved = [m for m in moved if m.endswith(s) and (s or not m.endswith(("-wal", "-shm", "-journal")))]
        assert len(moved) == 1, (s, moved)
        assert Path(moved[0]).read_bytes() == before[s]
    c = sqlite3.connect(dest)
    assert c.execute("SELECT COUNT(*) FROM widgets").fetchone() == (3,)
    c.close()


_HOLDER = (
    "import sqlite3,sys,time\n"
    "c=sqlite3.connect(sys.argv[1],isolation_level=None,timeout=0)\n"
    "c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO keep VALUES (43)')\n"
    "print('locked',flush=True); sys.stdin.read()\n"
)


def test_dbf1_busy_refuses_even_with_force(tmp_path):
    src, dest = _src(tmp_path), _good_dest(tmp_path)
    # Lock held by a separate process (fcntl locks are per-process, so reading
    # dest in this process cannot drop it).
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER, str(dest)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        before = _snapshot(dest)  # includes the holder's own -journal
        r = _run("--from", str(src), "--to", str(dest), "--apply", "--force", tmp_path=tmp_path)
        after = _snapshot(dest)
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)
    assert r.returncode == 3, r.stdout + r.stderr
    assert glob.glob(str(dest) + ".broken-*") == []
    assert after == before


def test_dbf1_small_garbage_dest_refused_without_force(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "dest.db"
    dest.write_bytes(b"junk" * 10)  # 40 bytes, no SQLite header, no siblings
    r = _run("--from", str(src), "--to", str(dest), "--apply", tmp_path=tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "--force" in out and str(dest) in out, out
    assert dest.read_bytes() == b"junk" * 10
    assert glob.glob(str(dest) + ".broken-*") == []


# ---- DBF-2
def _hot_journal(dest):
    j = Path(str(dest) + "-journal")
    j.write_bytes(b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7" + b"\x00" * 504)
    return j


def test_dbf2_hot_journal_message_names_file_without_force(tmp_path):
    src, dest = _src(tmp_path), _good_dest(tmp_path)
    j = _hot_journal(dest)
    r = _run("--from", str(src), "--to", str(dest), "--apply", tmp_path=tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert str(j) in out
    assert "--force" in out or "move" in out.lower()
    assert j.exists() and glob.glob(str(dest) + ".broken-*") == []


def test_dbf2_force_moves_hot_journal_aside(tmp_path):
    src, dest = _src(tmp_path), _good_dest(tmp_path)
    _hot_journal(dest)
    r = _run("--from", str(src), "--to", str(dest), "--apply", "--force", tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(glob.glob(str(dest) + ".broken-*-journal")) == 1
    assert not Path(str(dest) + "-journal").exists()
    c = sqlite3.connect(dest)
    assert c.execute("SELECT COUNT(*) FROM widgets").fetchone() == (3,)
    c.close()


# ---- DBF-3
def _wal_dest_with_stale_files(tmp_path):
    """A WAL db whose -wal/-shm are left behind with committed frames (copied while a writer is open)."""
    live = tmp_path / "live.db"
    c = sqlite3.connect(live)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE keep (x)")
    c.execute("INSERT INTO keep VALUES (1)")
    c.commit()
    dest = tmp_path / "dest.db"
    for s in SIB[:3]:
        shutil.copy2(str(live) + s, str(dest) + s)
    c.close()
    assert os.path.getsize(str(dest) + "-wal") > 0
    return dest


def test_dbf3_refused_busy_run_leaves_wal_and_shm_untouched(tmp_path):
    src, dest = _src(tmp_path), _wal_dest_with_stale_files(tmp_path)
    before = {s: (Path(str(dest) + s).read_bytes(), os.stat(str(dest) + s).st_mtime_ns)
              for s in SIB[:3]}
    fd = os.open(dest, os.O_RDWR)
    fcntl.lockf(fd, fcntl.LOCK_EX)  # another process holds a lock on the db
    try:
        r = _run("--from", str(src), "--to", str(dest), "--apply", tmp_path=tmp_path)
    finally:
        os.close(fd)
    assert r.returncode == 3, r.stdout + r.stderr
    for s in SIB[:3]:
        p = str(dest) + s
        assert os.path.exists(p), f"{p} was removed by the probe"
        assert (Path(p).read_bytes(), os.stat(p).st_mtime_ns) == before[s], f"{p} was mutated"
    assert glob.glob(str(dest) + ".broken-*") == []


def test_dbf3_probe_is_readonly_and_takes_no_exclusive_transaction():
    src = SCRIPT.read_text()
    assert "BEGIN EXCLUSIVE" not in src
    assert "mode=ro" in src


# ---- DBF-4
def test_dbf4_post_install_integrity_failure_names_broken_files(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("restore_db_t50f", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src, dest = _src(tmp_path), _good_dest(tmp_path)

    real = sqlite3

    class Conn:
        def __init__(self, c):
            self._c = c

        def execute(self, sql, *a):
            if "integrity_check" in sql:
                return real.connect(":memory:").execute("SELECT 'corrupt'")
            return self._c.execute(sql, *a)

        def __getattr__(self, n):
            return getattr(self._c, n)

    class FakeSqlite:
        def __getattr__(self, n):
            return getattr(real, n)

        def connect(self, path, *a, **k):
            c = real.connect(path, *a, **k)
            if str(path) == str(dest) and not glob.glob(str(dest) + ".broken-*"):
                return c
            return Conn(c) if str(path) == str(dest) else c

    monkeypatch.setattr(mod, "sqlite3", FakeSqlite())
    monkeypatch.setattr(sys, "argv", ["restore_db.py", "--from", str(src), "--to", str(dest), "--apply"])
    rc = mod.main()
    cap = capsys.readouterr()
    assert rc != 0
    err = cap.err  # the failure message itself, not the earlier "moved aside" progress lines
    assert "failed integrity_check" in err
    broken = glob.glob(str(dest) + ".broken-*")
    assert broken, "setup: nothing was moved aside"
    assert str(dest) + ".broken-" in err or os.path.basename(str(dest)) + ".broken-" in err


# ---- DBF-5
def test_dbf5_unreadable_proc_mounts_falls_back_to_delete_and_warns(tmp_path, monkeypatch, caplog):
    from backend import db
    real_open = builtins.open

    def fake_open(file, *a, **k):
        if str(file) == "/proc/mounts":
            raise PermissionError(13, "denied", "/proc/mounts")
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    with caplog.at_level(logging.WARNING):
        mode = db.journal_mode_for(tmp_path / "act.db")
    assert mode == "DELETE"
    assert any(r.levelno >= logging.WARNING and "mounts" in r.getMessage().lower()
               for r in caplog.records)


def test_dbf5_readable_mounts_still_wal_for_local(tmp_path):
    from backend import db
    assert db.journal_mode_for(tmp_path / "act.db", [("/", "ext4")]) == "WAL"
