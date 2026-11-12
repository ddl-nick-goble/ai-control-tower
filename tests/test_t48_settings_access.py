"""T-48: Settings reachable in a Domino Workspace (SR-2, SR-4, SR-5) and demo-switch fixes (SF-1..SF-3).
Written from the AC text and interface contract only."""
import importlib
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Domino-User-Id": "u-owner", "X-Domino-User": "owner"}
LISTED = {"X-Domino-User-Id": "u-ann", "X-Domino-User": "ann"}
OTHER = {"X-Domino-User-Id": "u-bob", "X-Domino-User": "bob"}
WS_PATH = "/mnt/runs/notebookSession/abc123"
SECRET = "S3cr3t-PLAINTEXT-value-9f8e7d"

ENV_NAMES = ["ACT_SECRET_KEY", "ACT_DEMO_MODE", "ACT_ADMINS", "LLM_GATEWAY_URL", "ACT_LLM_PROVIDER",
             "BREAK_CLASSIFIER_ENDPOINT_URL", "BREAK_CLASSIFIER_ACCESS_KEY", "ACT_EXTERNAL_ALLOW_HOSTS",
             "ACT_TRUSTED_JWKS_PATH", "ACT_SERVICE_ACCOUNTS", "ACT_POLICY_AGENTIC_ID", "ACT_POLICY_MODEL_ID",
             "ACT_WORKSPACE_OWNER_MODE", "DOMINO_RUN_HOST_PATH"]


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


def _start(env):
    from cryptography.fernet import Fernet
    env.setenv("ACT_SECRET_KEY", Fernet.generate_key().decode())
    _purge()
    return importlib.import_module("backend.main")


@pytest.fixture
def client(env):
    main = _start(env)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c
    _purge()


@pytest.fixture
def demo_client(env, tmp_path):
    main = _start(env)
    from backend import demo
    d = tmp_path / "demo-ds"
    d.mkdir()
    demo.DATASET_DIR = d
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c
    _purge()


def _req(headers=None):
    return SimpleNamespace(headers=headers or {})


def _stored_demo(c):
    r = c.get("/api/settings", headers=ADMIN)
    assert r.status_code == 200, r.text
    return next(s for s in r.json()["settings"] if s["name"] == "demo_mode")["value"]


# ---- SR-2 workspace owner mode ----
def _mode():
    from backend import domino_identity
    return domino_identity.workspace_owner_mode()


@pytest.mark.parametrize("val,want", [("1", True), ("true", True), ("0", False), ("false", False)])
def test_sr2_explicit_env_wins(env, val, want):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", val)
    # opposite of what auto-detect would give
    if want is False:
        env.setenv("DOMINO_RUN_HOST_PATH", WS_PATH)
    assert _mode() is want


def test_sr2_auto_on_in_workspace_with_owner(env):
    env.setenv("DOMINO_RUN_HOST_PATH", WS_PATH)
    assert _mode() is True


def test_sr2_auto_off_without_run_host_path(env):
    assert _mode() is False
    env.setenv("DOMINO_RUN_HOST_PATH", "")
    assert _mode() is False


def test_sr2_auto_off_for_job_run_path(env):
    env.setenv("DOMINO_RUN_HOST_PATH", "/mnt/runs/jobs/abc")
    assert _mode() is False


def test_sr2_auto_off_without_project_owner(env):
    env.setenv("DOMINO_RUN_HOST_PATH", WS_PATH)
    env.delenv("DOMINO_PROJECT_OWNER")
    assert _mode() is False


def test_sr2_get_user_returns_owner_when_on(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "1")
    from backend import domino_identity
    u = domino_identity.get_user(_req())
    assert u is not None and u.username == "owner"


def test_sr2_headers_win_over_owner_mode(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "1")
    from backend import domino_identity
    u = domino_identity.get_user(_req({"X-Domino-User-Id": "u-bob", "X-Domino-User": "bob"}))
    assert u is not None and u.username == "bob"


def test_sr2_bearer_wins_over_owner_mode(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "1")
    from backend import domino_identity
    env.setattr(domino_identity, "_verify_bearer", lambda t: domino_identity.User("u-c", "carl"))
    u = domino_identity.get_user(_req({"Authorization": "Bearer abc"}))
    assert u is not None and u.username == "carl"


def test_sr2_off_get_user_anonymous_is_none(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "0")
    from backend import domino_identity
    assert domino_identity.get_user(_req()) is None


def test_sr2_access_via_workspace_for_owner(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "1")
    main = _start(env)
    with TestClient(main.app) as c:
        r = c.get("/api/settings/access")
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["admin"] is True and b["admins_configured"] is True
        assert b["user"] == "owner" and b["via"] == "workspace"
        assert c.get("/api/settings").status_code == 200
    _purge()


def test_sr2_access_via_domino_with_headers(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "1")
    main = _start(env)
    with TestClient(main.app) as c:
        b = c.get("/api/settings/access", headers=OTHER).json()
        assert b["user"] == "bob" and b["via"] == "domino" and b["admin"] is False
        b = c.get("/api/settings/access", headers=ADMIN).json()
        assert b["user"] == "owner" and b["via"] == "domino" and b["admin"] is True
    _purge()


def test_sr2_off_access_anonymous_401(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "0")
    main = _start(env)
    with TestClient(main.app) as c:
        assert c.get("/api/settings/access").status_code == 401
        assert c.get("/api/settings").status_code == 401
    _purge()


def test_sr2_default_not_workspace_fails_closed(client):
    assert client.get("/api/settings/access").status_code == 401
    assert client.post("/api/agents/external", json={}).status_code == 401


def test_sr2_off_write_route_anonymous_401(env):
    env.setenv("ACT_WORKSPACE_OWNER_MODE", "false")
    env.setenv("DOMINO_RUN_HOST_PATH", WS_PATH)
    main = _start(env)
    with TestClient(main.app) as c:
        assert c.post("/api/agents/external", json={}).status_code == 401
    _purge()


# ---- SR-4 ----
def test_sr4_lists_exactly_defs_with_flags(env):
    env.setenv("LLM_GATEWAY_URL", "http://gw.example")
    env.setenv("BREAK_CLASSIFIER_ACCESS_KEY", SECRET)
    main = _start(env)
    from backend import settings
    with TestClient(main.app) as c:
        r = c.get("/api/settings", headers=ADMIN)
        assert r.status_code == 200
        items = {s["name"]: s for s in r.json()["settings"]}
        assert set(items) == set(settings.DEFS)
        assert len(r.json()["settings"]) == len(settings.DEFS)
        for n, s in items.items():
            assert s["section"] == settings.DEFS[n]["section"]
            assert s["section"] in ("demo", "integrations", "credentials", "access")
            assert s["secret"] is settings.DEFS[n]["secret"]
        assert items["llm_gateway_url"]["source"] == "env" and items["llm_gateway_url"]["locked"] is True
        assert items["policy_model_id"]["source"] != "env"
        assert items["break_classifier_access_key"]["is_set"] is True
        assert SECRET not in r.text
    _purge()


def test_sr4_stored_secret_never_in_clear(client):
    r = client.put("/api/settings/aws_secret_access_key", json={"value": SECRET}, headers=ADMIN)
    assert r.status_code == 200 and SECRET not in r.text
    r = client.get("/api/settings", headers=ADMIN)
    assert SECRET not in r.text


# ---- SR-5 ----
def test_sr5_admins_list_and_owner_stays_admin(client):
    assert client.get("/api/settings/access", headers=LISTED).json()["admin"] is False
    r = client.put("/api/settings/admins", json={"value": "ann, carl"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert client.get("/api/settings/access", headers=LISTED).json()["admin"] is True
    assert client.get("/api/settings/access", headers=ADMIN).json()["admin"] is True  # owner not in the list
    assert client.get("/api/settings/access", headers=OTHER).json()["admin"] is False
    assert client.delete("/api/settings/admins", headers=ADMIN).status_code == 200
    assert client.get("/api/settings/access", headers=ADMIN).json()["admin"] is True
    assert client.get("/api/settings/access", headers=LISTED).json()["admin"] is False


# ---- SF-1 DemoGuardError never 500 ----
def _demo_on(c):
    r = c.put("/api/settings/demo_mode", json={"value": True}, headers=ADMIN)
    assert r.status_code == 200, r.text
    from backend import demo
    assert demo.enabled()


def _fail_switch_off_once(env):
    from backend import demo
    real = demo.switch_off
    state = {"n": 0}

    def flaky(own=0):
        state["n"] += 1
        if state["n"] == 1:
            raise demo.DemoGuardError("requests still running; try again")
        return real(own)
    env.setattr(demo, "switch_off", flaky)
    return state


def _check_conflict(r):
    assert r.status_code in (409, 503), (r.status_code, r.text)
    assert "try again" in r.text.lower()


def test_sf1_delete_demo_mode_guard_error(demo_client, env):
    _demo_on(demo_client)
    _fail_switch_off_once(env)
    _check_conflict(demo_client.delete("/api/settings/demo_mode", headers=ADMIN))
    from backend import demo
    assert _stored_demo(demo_client) is demo.enabled()


def test_sf1_put_false_guard_error(demo_client, env):
    _demo_on(demo_client)
    _fail_switch_off_once(env)
    _check_conflict(demo_client.put("/api/settings/demo_mode", json={"value": False}, headers=ADMIN))
    from backend import demo
    assert _stored_demo(demo_client) is demo.enabled()


def test_sf1_store_failure_rollback_guard_error(demo_client, env):
    from backend import settings, demo
    _fail_switch_off_once(env)

    def boom(*a, **k):
        raise RuntimeError("db write failed")
    env.setattr(settings, "_store", boom)
    r = demo_client.put("/api/settings/demo_mode", json={"value": True}, headers=ADMIN)
    assert r.status_code != 500 or "try again" in r.text.lower(), r.text
    assert r.status_code in (409, 503), (r.status_code, r.text)
    assert "try again" in r.text.lower()
    # stored setting was never saved, so live demo must not be left on
    assert demo.enabled() is False


# ---- SF-2 simultaneous toggles ----
@pytest.mark.parametrize("pair", [(True, False), (True, True), (False, False)])
def test_sf2_simultaneous_toggles_do_not_stall(demo_client, pair):
    from backend import demo
    c = demo_client
    results = []
    barrier = threading.Barrier(2)

    def go(v):
        barrier.wait()
        t = time.time()
        r = c.put("/api/settings/demo_mode", json={"value": v}, headers=ADMIN)
        results.append((r.status_code, time.time() - t))
    ts = [threading.Thread(target=go, args=(v,)) for v in pair]
    t0 = time.time()
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert time.time() - t0 < 5, results
    assert len(results) == 2 and all(s != 500 for s, _ in results), results
    assert _stored_demo(c) is demo.enabled()


def test_sf2_simultaneous_deletes_do_not_stall(demo_client):
    from backend import demo
    c = demo_client
    _demo_on(c)
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        results.append(c.delete("/api/settings/demo_mode", headers=ADMIN).status_code)
    ts = [threading.Thread(target=go) for _ in range(2)]
    t0 = time.time()
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert time.time() - t0 < 5, results
    assert all(s != 500 for s in results), results
    assert _stored_demo(c) is demo.enabled()


# ---- SF-3 demo run must not hold the runtime lock ----
def test_sf3_demo_run_does_not_hold_rt_lock(demo_client, env):
    from backend import demo
    from jobs import seed_demo
    c = demo_client
    _demo_on(c)
    agents = c.get("/api/agents").json()
    agent_id = next(a["id"] for a in agents if a["name"] == "recon-agent")
    started, release = threading.Event(), threading.Event()

    def slow(agent_id, eng=None, *a, **k):
        started.set()
        release.wait(20)
        return "run-stub"
    env.setattr(seed_demo, "run_recon_once", slow)
    out = {}
    t = threading.Thread(target=lambda: out.update(r=c.post(f"/api/demo/run/{agent_id}", headers=ADMIN)))
    t.start()
    try:
        assert started.wait(10), "stubbed run never started"
        got = []

        def other():
            ok = demo._rt_lock.acquire(timeout=2)
            got.append(ok)
            if ok:
                demo._rt_lock.release()
        o = threading.Thread(target=other)
        t0 = time.time()
        o.start()
        o.join(5)
        assert got == [True], "demo runtime lock is held for the whole recon run"
        assert time.time() - t0 < 2.5
    finally:
        release.set()
        t.join(20)
    assert out["r"].status_code == 200, out["r"].text
