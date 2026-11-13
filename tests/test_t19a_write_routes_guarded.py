"""T-19a: no anonymous write route other than the tier preview is reachable."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient

OPEN = {("POST", "/api/tiering/preview")}


@pytest.fixture
def client(db_env, monkeypatch):
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("ACT_SETTINGS_OPEN", "0")  # old lock: Settings writes 401 anonymous
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


def test_p1_other_write_routes_still_401_anonymous(client):
    routes = []
    for r in client.app.routes:
        for m in getattr(r, "methods", None) or ():
            if m in {"POST", "PUT", "PATCH", "DELETE"} and r.path.startswith(("/api", "/v1")):
                routes.append((m, r.path))
    assert routes
    bad = []
    for m, p in routes:
        if (m, p) in OPEN:
            continue
        url = p.replace("{agent_id}", "x").replace("{run_id}", "x").replace("{name}", "x")
        resp = client.request(m, url, json={})
        if resp.status_code not in (401, 403):
            bad.append((m, p, resp.status_code))
    assert not bad, bad
