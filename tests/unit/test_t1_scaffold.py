import importlib
import sqlite3
import sys

import pytest

TABLES = {
    "agents", "agent_versions", "manifests", "identities", "dependencies", "runs",
    "events", "findings", "approvals", "validation_records", "evidence_packs",
    "audit_events",
}


def _tables(path):
    con = sqlite3.connect(str(path))
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def test_t1_health_ok(db_mod):
    from fastapi.testclient import TestClient
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_t1_init_db_creates_twelve_tables(db_mod, db_env):
    db_mod.init_db()
    assert TABLES <= _tables(db_env)


def test_t1_init_db_idempotent(db_mod, db_env):
    db_mod.init_db()
    db_mod.init_db()
    assert TABLES <= _tables(db_env)


def test_t1_events_has_hash_chain_columns(db_mod, db_env):
    db_mod.init_db()
    con = sqlite3.connect(str(db_env))
    cols = {r[1] for r in con.execute("pragma table_info(events)")}
    con.close()
    assert {"prev_hash", "hash"} <= cols


def test_t1_pragmas_on_connection(db_mod):
    db_mod.init_db()
    from sqlalchemy import text
    with db_mod.engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000


def test_t1_engine_queuepool_sizes(db_mod):
    from sqlalchemy.pool import QueuePool
    pool = db_mod.engine.pool
    assert isinstance(pool, QueuePool)
    assert pool.size() == 20
    assert pool._max_overflow == 5


def test_t1_db_path_env_override(db_mod, db_env):
    assert str(db_env) in str(db_mod.engine.url)


def _fresh_db(monkeypatch):
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    return importlib.import_module("backend.db")


def test_t1_db_path_git_based_domino(tmp_path, monkeypatch):
    monkeypatch.delenv("ACT_DB", raising=False)
    monkeypatch.setenv("ACT_DB_PREWARM_COUNT", "0")
    monkeypatch.setenv("DOMINO_IS_GIT_BASED", "true")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "proj1")
    mod = _fresh_db(monkeypatch)
    assert "/mnt/data/proj1/act/act.db" in str(mod.engine.url)


def test_t1_db_path_local_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ACT_DB", raising=False)
    for k in ("DOMINO_IS_GIT_BASED", "DOMINO_PROJECT_NAME", "DOMINO_API_PROXY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ACT_DB_PREWARM_COUNT", "0")
    monkeypatch.chdir(tmp_path)
    mod = _fresh_db(monkeypatch)
    assert str(mod.engine.url).endswith("act.db")


def test_t1_require_user_rejects_anonymous(db_env):
    from fastapi import HTTPException
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    ident = importlib.import_module("backend.domino_identity")
    assert hasattr(ident, "require_user")
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    app = FastAPI()

    @app.post("/w")
    def w(u=Depends(ident.require_user)):
        return {"user_id": u.user_id, "username": u.username}

    c = TestClient(app)
    assert c.post("/w").status_code == 401
    assert c.put("/w").status_code in (401, 405)  # route is POST only; 405 acceptable
    r = c.post("/w", headers={"X-Domino-User-Id": "u1", "X-Domino-User": "alice"})
    assert r.status_code == 200
    assert r.json() == {"user_id": "u1", "username": "alice"}


def test_t1_ac_write_guard_rejects_anonymous_everywhere(db_mod):
    from fastapi.testclient import TestClient
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        assert c.post("/api/does-not-exist").status_code == 401
        assert c.post("/v1/traces").status_code == 401
        assert c.delete("/api/agents/x").status_code == 401
        assert c.put("/api/does-not-exist").status_code == 401
        assert c.patch("/api/does-not-exist").status_code == 401


def test_t1_ac_write_guard_passes_with_identity(db_mod):
    from fastapi.testclient import TestClient
    main = importlib.import_module("backend.main")
    h = {"X-Domino-User-Id": "u1", "X-Domino-User": "alice"}
    with TestClient(main.app) as c:
        assert c.post("/api/does-not-exist", headers=h).status_code == 404


def test_t1_ac_health_anonymous_ok(db_mod):
    from fastapi.testclient import TestClient
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        assert c.get("/api/health").status_code == 200


def test_t1_ac_events_without_run_row(db_mod):
    from sqlalchemy import insert
    db_mod.init_db()
    with db_mod.engine.begin() as conn:
        conn.execute(insert(db_mod.events).values(
            run_id="no-such-run", agent_id="shadow", ts="2026-01-01T00:00:00Z",
            kind="span", raw_span={}, event={}, prev_hash=None, hash="h1"))
        n = conn.execute(db_mod.events.select()).fetchall()
    assert len(n) == 1
