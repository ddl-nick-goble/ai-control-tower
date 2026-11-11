"""T-39: in-app Settings (ST-2..ST-6). Written from the spec/interface contract only.

Assumptions: demo DB location is backend.demo.default_db_path(), redirected here via
backend.demo.DATASET_DIR -> tmp dir; real DB is ACT_DB.
"""
import importlib
import logging
import sqlite3
import stat
import sys
import time

import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Domino-User-Id": "u-owner", "X-Domino-User": "owner"}
OTHER = {"X-Domino-User-Id": "u-bob", "X-Domino-User": "bob"}
LISTED = {"X-Domino-User-Id": "u-ann", "X-Domino-User": "ann"}
SECRET = "S3cr3t-PLAINTEXT-value-9f8e7d"

ENV_NAMES = ["ACT_SECRET_KEY", "ACT_DEMO_MODE", "ACT_ADMINS", "LLM_GATEWAY_URL", "ACT_LLM_PROVIDER",
             "BREAK_CLASSIFIER_ENDPOINT_URL", "BREAK_CLASSIFIER_ACCESS_KEY", "ACT_EXTERNAL_ALLOW_HOSTS",
             "ACT_TRUSTED_JWKS_PATH", "ACT_SERVICE_ACCOUNTS", "ACT_POLICY_AGENTIC_ID", "ACT_POLICY_MODEL_ID"]


def _purge():
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


@pytest.fixture
def env(db_env, tmp_path, monkeypatch):
    for k in ENV_NAMES:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "owner")
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("ACT_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")
    return monkeypatch


def _start(env, key=True):
    if key:
        from cryptography.fernet import Fernet
        env.setenv("ACT_SECRET_KEY", Fernet.generate_key().decode())
    _purge()
    return importlib.import_module("backend.main")


@pytest.fixture
def client(env):
    main = _start(env)
    with TestClient(main.app) as c:
        yield c
    _purge()


def _entry(c, name, headers=ADMIN):
    r = c.get("/api/settings", headers=headers)
    assert r.status_code == 200, r.text
    return next(s for s in r.json()["settings"] if s["name"] == name)


def _dump(path):
    cx = sqlite3.connect(path)
    try:
        return "\n".join(cx.iterdump())
    finally:
        cx.close()


# ---- ST-6 access ----
def test_st6_anonymous_401(client):
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings/llm_gateway_url", json={"value": "http://x"}).status_code == 401
    assert client.delete("/api/settings/llm_gateway_url").status_code == 401


def test_st6_non_admin_403(client):
    assert client.get("/api/settings", headers=OTHER).status_code == 403
    assert client.put("/api/settings/llm_gateway_url", json={"value": "http://x"}, headers=OTHER).status_code == 403
    assert client.delete("/api/settings/llm_gateway_url", headers=OTHER).status_code == 403


def test_st6_owner_can_view_and_shape(client):
    r = client.get("/api/settings", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["key_source"] in ("env", "file")
    assert isinstance(body["warnings"], list)
    names = {s["name"] for s in body["settings"]}
    for n in ["demo_mode", "admins", "llm_provider", "llm_gateway_url", "break_classifier_endpoint_url",
              "break_classifier_access_key", "external_allow_hosts", "trusted_jwks_path", "service_accounts",
              "policy_agentic_id", "policy_model_id", "aws_role_arn", "aws_external_id", "aws_access_key_id",
              "aws_secret_access_key", "google_service_account_json", "microsoft_tenant_id",
              "microsoft_client_id", "microsoft_client_secret", "databricks_host", "databricks_client_id",
              "databricks_client_secret"]:
        assert n in names, n
    for s in body["settings"]:
        assert s["section"] in ("demo", "credentials", "integrations", "access")
        assert s["source"] in ("app", "env", "default")
        assert isinstance(s["secret"], bool) and isinstance(s["is_set"], bool) and isinstance(s["locked"], bool)
        if s["secret"]:
            assert "value" not in s


def test_st6_listed_admin_gets_access_after_admins_set(client):
    assert client.get("/api/settings", headers=LISTED).status_code == 403
    r = client.put("/api/settings/admins", json={"value": "ann, carl"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert client.get("/api/settings", headers=LISTED).status_code == 200
    assert client.get("/api/settings", headers=OTHER).status_code == 403


def test_st6_admins_from_env(env):
    env.setenv("ACT_ADMINS", "ann")
    main = _start(env)
    with TestClient(main.app) as c:
        assert c.get("/api/settings", headers=LISTED).status_code == 200
        assert c.get("/api/settings", headers=OTHER).status_code == 403
    _purge()


# ---- PUT/DELETE errors ----
def test_unknown_name_404(client):
    assert client.put("/api/settings/policy_model_id", json={"value": "m"}, headers=ADMIN).status_code == 200
    assert client.put("/api/settings/nope_not_real", json={"value": "x"}, headers=ADMIN).status_code == 404


def test_invalid_value_422(client):
    assert client.put("/api/settings/demo_mode", json={"value": "banana"}, headers=ADMIN).status_code == 422
    assert client.put("/api/settings/llm_gateway_url", json={}, headers=ADMIN).status_code == 422


# ---- ST-2 secrets ----
def test_st2_secret_round_trip(client, db_env):
    r = client.put("/api/settings/aws_secret_access_key", json={"value": SECRET}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert SECRET not in r.text
    e = _entry(client, "aws_secret_access_key")
    assert e["is_set"] is True and "value" not in e and e["secret"] is True
    assert e["source"] == "app" and e["changed_by"] == "owner" and e["changed_at"]
    assert SECRET not in client.get("/api/settings", headers=ADMIN).text
    from backend.settings import get_setting
    assert get_setting("aws_secret_access_key") == SECRET
    dump = _dump(str(db_env))
    assert SECRET not in dump
    assert SECRET.encode() not in open(db_env, "rb").read()


def test_st2_clear_secret(client):
    client.put("/api/settings/microsoft_client_secret", json={"value": SECRET}, headers=ADMIN)
    r = client.delete("/api/settings/microsoft_client_secret", headers=ADMIN)
    assert r.status_code == 200, r.text
    e = _entry(client, "microsoft_client_secret")
    assert e["is_set"] is False
    from backend.settings import get_setting
    assert not get_setting("microsoft_client_secret")


def test_st2_no_secret_in_audit_or_logs(client, db_env, caplog):
    caplog.set_level(logging.DEBUG)
    client.put("/api/settings/databricks_client_secret", json={"value": SECRET}, headers=ADMIN)
    client.put("/api/settings/databricks_client_secret", json={"value": SECRET + "2"}, headers=ADMIN)
    client.get("/api/settings", headers=ADMIN)
    client.delete("/api/settings/databricks_client_secret", headers=ADMIN)
    assert "databricks_client_secret" in _dump(str(db_env))  # an audit event was written
    from backend import db
    with db.engine.connect() as cx:
        rows = [str(tuple(r)) for r in cx.execute(db.audit_events.select()).all()]
    assert any("databricks_client_secret" in r for r in rows)
    assert not any(SECRET in r for r in rows)
    assert SECRET not in caplog.text


def test_st2_key_file_created_0600_when_no_env_key(env, db_env):
    main = _start(env, key=False)
    with TestClient(main.app) as c:
        body = c.get("/api/settings", headers=ADMIN).json()
        assert body["key_source"] == "file"
        assert any("ACT_SECRET_KEY" in w for w in body["warnings"])
        assert c.put("/api/settings/aws_access_key_id", json={"value": SECRET}, headers=ADMIN).status_code == 200
    keyfiles = [p for p in db_env.parent.iterdir() if p != db_env and p.is_file()
                and not p.name.startswith(db_env.name) and "key" in p.name.lower()]
    assert len(keyfiles) == 1, list(db_env.parent.iterdir())
    assert stat.S_IMODE(keyfiles[0].stat().st_mode) == 0o600
    first = keyfiles[0].read_bytes()
    _purge()
    main = importlib.import_module("backend.main")  # restart: key generated once, reused
    with TestClient(main.app) as c:
        from backend.settings import get_setting
        assert get_setting("aws_access_key_id") == SECRET
    assert keyfiles[0].read_bytes() == first
    _purge()


def test_st2_env_key_used_no_file_no_warning(env, db_env):
    main = _start(env, key=True)
    with TestClient(main.app) as c:
        body = c.get("/api/settings", headers=ADMIN).json()
        assert body["key_source"] == "env"
        assert not any("ACT_SECRET_KEY" in w for w in body["warnings"])
        c.put("/api/settings/aws_access_key_id", json={"value": SECRET}, headers=ADMIN)
    others = [p for p in db_env.parent.iterdir() if "key" in p.name.lower()]
    assert others == []
    _purge()


# ---- ST-3 precedence / lock ----
def test_st3_env_wins_and_locks(env):
    env.setenv("LLM_GATEWAY_URL", "http://from-env.example")
    main = _start(env)
    with TestClient(main.app) as c:
        e = _entry(c, "llm_gateway_url")
        assert e["source"] == "env" and e["locked"] is True
        r = c.put("/api/settings/llm_gateway_url", json={"value": "http://app.example"}, headers=ADMIN)
        assert r.status_code == 423
        from backend.settings import get_setting
        assert get_setting("llm_gateway_url") == "http://from-env.example"
    _purge()


def test_st3_secret_env_locked_no_value_exposed(env):
    env.setenv("BREAK_CLASSIFIER_ACCESS_KEY", SECRET)
    main = _start(env)
    with TestClient(main.app) as c:
        r = c.get("/api/settings", headers=ADMIN)
        assert SECRET not in r.text
        e = _entry(c, "break_classifier_access_key")
        assert e["source"] == "env" and e["locked"] is True and e["is_set"] is True
        assert c.put("/api/settings/break_classifier_access_key", json={"value": "z"}, headers=ADMIN).status_code == 423
    _purge()


# ---- ST-4 live + audit ----
def test_st4_live_change_without_reload(client):
    from backend.settings import get_setting
    t0 = time.time()
    r = client.put("/api/settings/llm_gateway_url", json={"value": "http://live.example/v1"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "llm_gateway_url"
    assert get_setting("llm_gateway_url") == "http://live.example/v1"
    assert time.time() - t0 < 5
    e = _entry(client, "llm_gateway_url")
    assert e["value"] == "http://live.example/v1" and e["source"] == "app"
    client.delete("/api/settings/llm_gateway_url", headers=ADMIN)
    assert not get_setting("llm_gateway_url")
    assert _entry(client, "llm_gateway_url")["is_set"] is False


def test_st4_change_writes_audit_event_without_value(client):
    client.put("/api/settings/policy_model_id", json={"value": "model-abc-123"}, headers=ADMIN)
    from backend import db
    with db.engine.connect() as cx:
        rows = [r for r in cx.execute(db.audit_events.select()).all() if "policy_model_id" in str(tuple(r))]
    assert rows
    assert all("model-abc-123" not in str(tuple(r)) for r in rows)
    assert any(r.actor_username == "owner" and r.ts for r in rows)


# ---- ST-5 demo switch ----
@pytest.fixture
def demo_client(env, tmp_path):
    main = _start(env)
    from backend import demo
    d = tmp_path / "demo-ds"
    d.mkdir()
    demo.DATASET_DIR = d
    with TestClient(main.app) as c:
        yield c
    _purge()


def _wait_demo(c, want, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if c.get("/api/health").json()["demo_mode"] is want:
            return True
        time.sleep(0.2)
    return False


def test_st5_demo_toggle_on_off_live(demo_client, db_env):
    c = demo_client
    assert c.get("/api/health").json()["demo_mode"] is False
    real_before = _dump(str(db_env))
    assert c.get("/api/agents").json() == []
    r = c.put("/api/settings/demo_mode", json={"value": True}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert _wait_demo(c, True)
    assert len(c.get("/api/agents").json()) > 0  # demo DB seeded
    assert _dump(str(db_env)) == real_before or "recon-agent" not in _dump(str(db_env))
    from backend import db
    with db.engine.connect() as cx:
        assert cx.execute(db.agents.select()).all()
    # off: back to real (empty) DB
    assert c.put("/api/settings/demo_mode", json={"value": False}, headers=ADMIN).status_code == 200
    assert _wait_demo(c, False)
    assert c.get("/api/agents").json() == []
    assert "recon-agent" not in _dump(str(db_env))


def test_st5_demo_reset_never_touches_real_db(demo_client, db_env):
    c = demo_client
    c.put("/api/settings/demo_mode", json={"value": True}, headers=ADMIN)
    assert _wait_demo(c, True)
    c.post("/api/demo/reset", headers=ADMIN)
    c.put("/api/settings/demo_mode", json={"value": False}, headers=ADMIN)
    assert _wait_demo(c, False)
    # the toggle setting row and audit live in real DB; no seeded agents
    from backend import db
    with db.engine.connect() as cx:
        assert cx.execute(db.agents.select()).all() == []


def test_st5_env_demo_mode_locks_switch(env):
    env.setenv("ACT_DEMO_MODE", "1")
    main = _start(env)
    with TestClient(main.app) as c:
        e = _entry(c, "demo_mode")
        assert e["source"] == "env" and e["locked"] is True
        assert c.put("/api/settings/demo_mode", json={"value": False}, headers=ADMIN).status_code == 423
        assert c.get("/api/health").json()["demo_mode"] is True
    _purge()
