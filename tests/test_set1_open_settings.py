"""SET-1: Settings open to everyone by default; ACT_SETTINGS_OPEN=0 restores the owner/admin lock.
Written from the AC text only."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Domino-User-Id": "u-owner", "X-Domino-User": "owner"}
OTHER = {"X-Domino-User-Id": "u-bob", "X-Domino-User": "bob"}
LISTED = {"X-Domino-User-Id": "u-ann", "X-Domino-User": "ann"}

ENV_NAMES = ["ACT_SECRET_KEY", "ACT_DEMO_MODE", "ACT_ADMINS", "LLM_GATEWAY_URL", "ACT_LLM_PROVIDER",
             "ACT_SETTINGS_OPEN", "ACT_WORKSPACE_OWNER_MODE", "DOMINO_RUN_HOST_PATH", "DOMINO_JWKS_URL"]


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


def _make(env):
    from cryptography.fernet import Fernet
    env.setenv("ACT_SECRET_KEY", Fernet.generate_key().decode())
    _purge()
    main = importlib.import_module("backend.main")
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def client(env):
    with _make(env) as c:
        yield c
    _purge()


def _changed_by(c, name="policy_model_id"):
    r = c.get("/api/settings", headers=ADMIN)
    assert r.status_code == 200, r.text
    return next(s for s in r.json()["settings"] if s["name"] == name)["changed_by"]


# ---- ST-1 ----
def test_ac_st1_anonymous_access_is_admin(client):
    r = client.get("/api/settings/access")
    assert r.status_code == 200
    assert r.json()["admin"] is True


def test_ac_st1_anonymous_list_put_delete_count(client):
    assert client.get("/api/settings").status_code == 200
    r = client.put("/api/settings/policy_model_id", json={"value": "m1"})
    assert r.status_code == 200, r.text
    r = client.delete("/api/settings/policy_model_id")
    assert r.status_code == 200, r.text
    assert client.get("/api/settings/real-agent-count").status_code == 200


def test_ac_st1_non_admin_user_is_admin(client):
    assert client.get("/api/settings/access", headers=OTHER).json()["admin"] is True
    assert client.get("/api/settings", headers=OTHER).status_code == 200
    assert client.get("/api/settings/real-agent-count", headers=OTHER).status_code == 200


def test_ac_st1_no_owner_configured_still_open(env):
    env.delenv("DOMINO_PROJECT_OWNER")
    with _make(env) as c:
        assert c.get("/api/settings/access").json()["admin"] is True
        assert c.get("/api/settings").status_code == 200
    _purge()


# ---- ST-2 ----
def test_ac_st2_changed_by_anonymous(client):
    r = client.put("/api/settings/policy_model_id", json={"value": "m1"})
    assert r.status_code == 200, r.text
    assert _changed_by(client) == "anonymous"


def test_ac_st2_changed_by_header_user(client):
    r = client.put("/api/settings/policy_model_id", json={"value": "m1"}, headers=OTHER)
    assert r.status_code == 200, r.text
    assert _changed_by(client) == "bob"


def test_ac_st2_changed_by_valid_bearer(env):
    with _make(env) as c:
        from backend import domino_identity
        env.setenv("DOMINO_JWKS_URL", "http://jwks.invalid")
        env.setattr(domino_identity, "_verify_bearer",
                    lambda t: domino_identity.User("u-c", "carl"))
        r = c.put("/api/settings/policy_model_id", json={"value": "m1"},
                  headers={"Authorization": "Bearer good"})
        assert r.status_code == 200, r.text
        assert _changed_by(c) == "carl"
    _purge()


def test_ac_st2_changed_by_workspace_owner_mode(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "1")
    with _make(env) as c:
        r = c.put("/api/settings/policy_model_id", json={"value": "m1"})
        assert r.status_code == 200, r.text
        assert _changed_by(c) == "owner"
    _purge()


# ---- ST-4 ----
@pytest.fixture
def locked(env):
    env.setenv("ACT_SETTINGS_OPEN", "0")
    env.setenv("ACT_ADMINS", "ann")
    with _make(env) as c:
        yield c
    _purge()


def test_ac_st4_closed_anonymous_401(locked):
    for url in ("/api/settings", "/api/settings/access", "/api/settings/real-agent-count"):
        assert locked.get(url).status_code == 401, url
    assert locked.put("/api/settings/policy_model_id", json={"value": "m1"}).status_code == 401
    assert locked.delete("/api/settings/policy_model_id").status_code == 401


def test_ac_st4_closed_non_admin_403(locked):
    assert locked.get("/api/settings", headers=OTHER).status_code == 403
    assert locked.get("/api/settings/real-agent-count", headers=OTHER).status_code == 403
    assert locked.put("/api/settings/policy_model_id", json={"value": "m1"}, headers=OTHER).status_code == 403
    assert locked.delete("/api/settings/policy_model_id", headers=OTHER).status_code == 403
    assert locked.get("/api/settings/access", headers=OTHER).json()["admin"] is False


def test_ac_st4_closed_owner_and_listed_admin_succeed(locked):
    for h in (ADMIN, LISTED):
        assert locked.get("/api/settings", headers=h).status_code == 200
        assert locked.get("/api/settings/access", headers=h).json()["admin"] is True
        assert locked.put("/api/settings/policy_model_id", json={"value": "m1"}, headers=h).status_code == 200


@pytest.mark.parametrize("val", ["1", "", "true", "yes", "off", "00"])
def test_ac_st4_other_values_are_open(env, val):
    env.setenv("ACT_SETTINGS_OPEN", val)
    with _make(env) as c:
        assert c.get("/api/settings/access").json()["admin"] is True
        assert c.get("/api/settings").status_code == 200
    _purge()


# ---- write guard stays closed for non-Settings routes ----
@pytest.mark.parametrize("method,url", [("post", "/api/demo/reset"), ("post", "/api/connections/x/scans"),
                                        ("post", "/api/staged/x/dismiss"), ("put", "/api/settingsx/a"),
                                        ("delete", "/api/other/a")])
def test_set1_non_settings_writes_still_401_anonymous_when_open(client, method, url):
    assert getattr(client, method)(url).status_code == 401


def test_set1_non_settings_writes_still_401_anonymous_when_closed(locked):
    assert locked.post("/api/demo/reset").status_code == 401
