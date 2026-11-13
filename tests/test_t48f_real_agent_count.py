"""T-48f: GET /api/settings/real-agent-count (SRF-3). Written from the AC text only."""
import importlib
import os
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Domino-User-Id": "u-owner", "X-Domino-User": "owner"}
OTHER = {"X-Domino-User-Id": "u-bob", "X-Domino-User": "bob"}
URL = "/api/settings/real-agent-count"


def _purge():
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


@pytest.fixture
def app(db_env, tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    for k in ("ACT_ADMINS", "ACT_DEMO_MODE", "ACT_WORKSPACE_OWNER_MODE", "DOMINO_RUN_HOST_PATH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "owner")
    monkeypatch.setenv("ACT_SETTINGS_OPEN", "0")  # SET-1: these tests describe the locked behaviour
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("ACT_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ACT_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")
    _purge()
    main = importlib.import_module("backend.main")
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c
    _purge()


def _point_real_db(path):
    from backend import db
    db.REAL_DB_PATH = str(path)
    db.real_engine = db._make_engine(str(path))
    return db


def test_ac_srf3_non_admin_gets_403(app):
    assert app.get(URL, headers=OTHER).status_code == 403


def test_ac_srf3_anonymous_gets_401(app):
    assert app.get(URL).status_code == 401


def test_ac_srf3_missing_real_db_returns_zero_and_creates_nothing(app, tmp_path):
    p = tmp_path / "nowhere" / "real.db"
    p.parent.mkdir()
    _point_real_db(p)
    r = app.get(URL, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["real_agent_count"] == 0
    assert not p.exists()


def test_ac_srf3_db_without_agents_table_returns_null(app, tmp_path):
    p = tmp_path / "empty.db"
    sqlite3.connect(p).execute("create table other (x int)").connection.commit()
    _point_real_db(p)
    r = app.get(URL, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["real_agent_count"] is None


def test_ac_srf3_garbage_db_returns_null(app, tmp_path):
    p = tmp_path / "garbage.db"
    p.write_bytes(os.urandom(4096))
    _point_real_db(p)
    r = app.get(URL, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["real_agent_count"] is None


@pytest.mark.parametrize("n", [1, 3])
def test_ac_srf3_exact_count_for_real_db(app, tmp_path, n):
    p = tmp_path / "real.db"
    db = _point_real_db(p)
    db.init_db(db.real_engine)
    with db.real_engine.begin() as cx:
        for i in range(n):
            cx.execute(db.agents.insert().values(id=f"act:x:{i}", source_platform="x", native_id=str(i),
                                                 name=f"a{i}"))
    r = app.get(URL, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["real_agent_count"] == n
