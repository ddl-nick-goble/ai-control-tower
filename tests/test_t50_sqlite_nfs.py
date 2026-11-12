import glob
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MOUNTS = [("/", "ext4"), ("/mnt/data", "nfs4"), ("/mnt/data/local", "ext4"),
          ("/mnt/cifs", "cifs"), ("/mnt/smb", "smb3"), ("/mnt/fuse", "fuse.gcsfuse"),
          ("/mnt/nfs3", "nfs")]
NFS_PATH = "/mnt/data/proj/act/act.db"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ACT_DB_JOURNAL_MODE", raising=False)
    monkeypatch.delenv("ACT_DB_POOL_SIZE", raising=False)


# ---- DB-1
def test_ac1_fs_type_longest_prefix(db_mod):
    assert db_mod.fs_type(NFS_PATH, MOUNTS) == "nfs4"
    assert db_mod.fs_type("/mnt/data/local/x/act.db", MOUNTS) == "ext4"
    assert db_mod.fs_type("/home/u/act.db", MOUNTS) == "ext4"


@pytest.mark.parametrize("p", ["/mnt/data/a.db", "/mnt/cifs/a.db", "/mnt/smb/a.db",
                               "/mnt/fuse/a.db", "/mnt/nfs3/a.db"])
def test_ac1_network_types_detected(db_mod, p):
    assert db_mod.is_network_fs(p, MOUNTS) is True
    assert db_mod.journal_mode_for(p, MOUNTS) == "DELETE"


def test_ac1_local_is_wal(db_mod):
    assert db_mod.is_network_fs("/home/u/a.db", MOUNTS) is False
    assert db_mod.journal_mode_for("/mnt/data/local/a.db", MOUNTS) == "WAL"


def test_ac1_override_case_insensitive(db_mod, monkeypatch):
    monkeypatch.setenv("ACT_DB_JOURNAL_MODE", "wal")
    assert db_mod.journal_mode_for(NFS_PATH, MOUNTS) == "WAL"
    monkeypatch.setenv("ACT_DB_JOURNAL_MODE", "Delete")
    assert db_mod.journal_mode_for("/home/u/a.db", MOUNTS) == "DELETE"


def test_ac1_invalid_override_ignored_with_warning(db_mod, monkeypatch, caplog):
    monkeypatch.setenv("ACT_DB_JOURNAL_MODE", "TRUNCATE")
    with caplog.at_level("WARNING"):
        assert db_mod.journal_mode_for(NFS_PATH, MOUNTS) == "DELETE"
        assert db_mod.journal_mode_for("/home/u/a.db", MOUNTS) == "WAL"
    assert any("ACT_DB_JOURNAL_MODE" in r.getMessage() for r in caplog.records)


def test_ac1_connection_uses_detected_mode(db_mod, monkeypatch):
    monkeypatch.setenv("ACT_DB_JOURNAL_MODE", "DELETE")
    eng = db_mod._make_engine(str(Path(db_mod.DB_PATH).parent / "d.db"))
    with eng.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "delete"
    eng.dispose()


# ---- DB-2
def test_ac2_pool_defaults(db_mod):
    assert db_mod.pool_size_for(NFS_PATH, MOUNTS) == 4
    assert db_mod.pool_size_for("/home/u/a.db", MOUNTS) == 20


def test_ac2_pool_override(db_mod, monkeypatch):
    monkeypatch.setenv("ACT_DB_POOL_SIZE", "7")
    assert db_mod.pool_size_for(NFS_PATH, MOUNTS) == 7
    assert db_mod.pool_size_for("/home/u/a.db", MOUNTS) == 7


def test_ac2_prewarm_capped_at_pool_size(db_env, monkeypatch):
    monkeypatch.setenv("ACT_DB_POOL_SIZE", "3")
    import importlib
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    db = importlib.import_module("backend.db")
    from sqlalchemy import event
    opened = []
    event.listen(db.engine, "connect", lambda *a: opened.append(1))
    db.prewarm(10)
    assert len(opened) <= 3
    assert len(opened) >= 1


# ---- DB-3
def test_ac3_check_open_clear_error(db_mod, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is definitely not a sqlite database" * 100)
    with pytest.raises(db_mod.DatabaseOpenError) as ei:
        db_mod.check_open(str(bad))
    msg = str(ei.value)
    assert str(bad) in msg
    assert db_mod.fs_type(str(bad)) in msg
    assert db_mod.journal_mode_for(str(bad)).upper() in msg.upper()
    assert "-shm" in msg and "-wal" in msg
    assert "stale" in msg.lower()


def test_ac3_check_open_ok_on_good_db(db_mod, tmp_path):
    good = tmp_path / "good.db"
    sqlite3.connect(good).close()
    db_mod.check_open(str(good))


# ---- DB-4
SCRIPT = ROOT / "scripts" / "restore_db.py"


def _run(*args, env_db=None):
    env = dict(os.environ)
    env.pop("ACT_DB_JOURNAL_MODE", None)
    if env_db:
        env["ACT_DB"] = str(env_db)
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


def test_ac4_dry_run_changes_nothing(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "dest.db"
    dest.write_bytes(b"existing")
    r = _run("--from", str(src), "--to", str(dest), env_db=tmp_path / "x.db")
    assert r.returncode == 0, r.stderr
    assert dest.read_bytes() == b"existing"
    assert glob.glob(str(dest) + ".broken-*") == []
    assert "widgets" in r.stdout and "3" in r.stdout


def test_ac4_refuses_corrupt_source(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"garbage not a database" * 500)
    dest = tmp_path / "dest.db"
    dest.write_bytes(b"existing")
    assert SCRIPT.exists()
    r = _run("--from", str(bad), "--to", str(dest), "--apply", env_db=tmp_path / "x.db")
    assert r.returncode != 0
    assert "No such file" not in r.stderr
    assert dest.read_bytes() == b"existing"
    assert glob.glob(str(dest) + ".broken-*") == []


def test_ac4_apply_moves_aside_copies_and_migrates(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "dest.db"
    dest.write_bytes(b"old-main")
    Path(str(dest) + "-wal").write_bytes(b"old-wal")
    Path(str(dest) + "-shm").write_bytes(b"old-shm")
    r = _run("--from", str(src), "--to", str(dest), "--apply", env_db=tmp_path / "x.db")
    assert r.returncode == 0, r.stdout + r.stderr
    main = glob.glob(str(dest) + ".broken-*")
    main = [p for p in main if not p.endswith(("-wal", "-shm"))]
    assert len(main) == 1
    stamp = main[0][len(str(dest)):]
    assert Path(main[0]).read_bytes() == b"old-main"
    assert Path(str(dest) + stamp + "-wal").read_bytes() == b"old-wal"
    assert Path(str(dest) + stamp + "-shm").read_bytes() == b"old-shm"
    c = sqlite3.connect(dest)
    assert c.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 3
    tables = {t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "agents" in tables  # schema init ran
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"  # local disk
    c.close()
    assert "widgets" in r.stdout
    assert src.exists()


def test_ac4_apply_without_existing_dest(tmp_path):
    src = _src(tmp_path)
    dest = tmp_path / "new.db"
    r = _run("--from", str(src), "--to", str(dest), "--apply", env_db=tmp_path / "x.db")
    assert r.returncode == 0, r.stdout + r.stderr
    assert dest.exists()


def test_ac4_to_defaults_to_app_db_path(tmp_path):
    src = _src(tmp_path)
    app_db = tmp_path / "appdb" / "act.db"
    app_db.parent.mkdir()
    r = _run("--from", str(src), "--apply", env_db=app_db)
    assert r.returncode == 0, r.stdout + r.stderr
    c = sqlite3.connect(app_db)
    assert c.execute("SELECT COUNT(*) FROM widgets").fetchone()[0] == 3
    c.close()


# ---- DB-5
def test_ac5_local_unchanged(db_mod):
    with db_mod.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert c.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
