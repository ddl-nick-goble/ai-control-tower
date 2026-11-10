"""T-16: Domino workspace/App proxy prefix handling (P-1..P-4)."""
import importlib
import shutil
import sys

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT

HDR = {"X-Domino-User-Id": "u16", "X-Domino-User": "t16"}
RUN = "/nick_goble/ai-control-tower/r/notebookSession/abc123run/"
BASE = RUN.rstrip("/")
PROXY = f"{BASE}/proxy/9888"
INDEX_HTML = "<!doctype html><html><body>T16-INDEX</body></html>"
ASSET_JS = "console.log('t16-asset');"


@pytest.fixture
def dist():
    """Ensure a built frontend exists (frontend/dist is a build artifact)."""
    d = ROOT / "frontend" / "dist"
    created = not d.exists()
    if created:
        (d / "assets").mkdir(parents=True)
        (d / "index.html").write_text(INDEX_HTML)
        (d / "assets" / "t16.js").write_text(ASSET_JS)
    yield d
    if created:
        shutil.rmtree(d)


@pytest.fixture
def client(db_env, monkeypatch, dist):
    monkeypatch.setenv("DOMINO_RUN_HOST_PATH", RUN)
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


def _asset_name(dist):
    return sorted((dist / "assets").iterdir())[0].name


def test_ac_p1_proxy_prefix_serves_index(client):
    r = client.get(f"{PROXY}/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.text == client.get("/").text


def test_ac_p1_proxy_prefix_serves_index_html(client):
    r = client.get(f"{PROXY}/index.html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_ac_p1_proxy_prefix_serves_assets(client, dist):
    name = _asset_name(dist)
    plain = client.get(f"/assets/{name}")
    assert plain.status_code == 200
    r = client.get(f"{PROXY}/assets/{name}")
    assert r.status_code == 200
    assert r.content == plain.content


def test_ac_p1_proxy_prefix_api_health(client):
    r = client.get(f"{PROXY}/api/health")
    assert r.status_code == 200
    assert r.json() == client.get("/api/health").json()


def test_ac_p1_proxy_prefix_other_port(client):
    r = client.get(f"{BASE}/proxy/8888/api/health")
    assert r.status_code == 200
    assert r.json() == client.get("/api/health").json()


def test_ac_p1_proxy_prefix_post_route_same_as_unprefixed(client):
    body = {"criteria": "not-a-dict"}
    plain = client.post("/api/agents/nope/tier", json=body, headers=HDR)
    r = client.post(f"{PROXY}/api/agents/nope/tier", json=body, headers=HDR)
    assert plain.status_code == 422
    assert r.status_code == plain.status_code
    assert r.json() == plain.json()


def test_ac_p1_proxy_prefix_post_write_guard_still_applies(client):
    r = client.post(f"{PROXY}/api/agents/nope/tier", json={"criteria": {}})
    assert r.status_code == 401


def test_ac_p2_published_prefix_serves_index(client):
    r = client.get(f"{BASE}/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_ac_p2_published_prefix_api_and_assets(client, dist):
    r = client.get(f"{BASE}/api/health")
    assert r.status_code == 200
    assert r.json() == client.get("/api/health").json()
    name = _asset_name(dist)
    assert client.get(f"{BASE}/assets/{name}").status_code == 200


def test_ac_p2_published_prefix_post_route(client):
    body = {"criteria": "x"}
    plain = client.post("/api/agents/nope/tier", json=body, headers=HDR)
    r = client.post(f"{BASE}/api/agents/nope/tier", json=body, headers=HDR)
    assert r.status_code == plain.status_code == 422


def test_ac_p3_unprefixed_unchanged(client):
    assert client.get("/").status_code == 200
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ac_p3_prefix_without_trailing_slash_redirects_or_serves(client):
    r = client.get(PROXY, follow_redirects=False)
    if r.status_code in (301, 302, 307, 308):
        loc = r.headers["location"]
        assert loc.rstrip("/").endswith("/proxy/9888")
        assert loc.endswith("/")
        r2 = client.get(loc)
        assert r2.status_code == 200 and "text/html" in r2.headers["content-type"]
    else:
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


def test_ac_p4_unknown_api_path_404_under_prefix(client):
    plain = client.get("/api/does-not-exist")
    assert plain.status_code == 404
    r = client.get(f"{PROXY}/api/does-not-exist")
    assert r.status_code == 404
    assert r.json() == plain.json()


def test_ac_p4_unknown_asset_404_under_prefix(client):
    assert client.get("/assets/nope.js").status_code == 404
    assert client.get(f"{PROXY}/assets/nope.js").status_code == 404


def test_ac_p4_unknown_unprefixed_still_404(client):
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/v1/does-not-exist").status_code == 404


def test_ac_p4_other_run_prefix_not_served(client):
    other = "/nick_goble/ai-control-tower/r/notebookSession/OTHERRUN/proxy/9888/api/health"
    assert client.get(other).status_code == 404


@pytest.mark.parametrize("path", [
    "/proxy/9888/../../etc/passwd",
    "/proxy/9888/%2e%2e/%2e%2e/etc/passwd",
    "/proxy/9888/assets/..%2f..%2f..%2fbackend/main.py",
    "/proxy/9888/..%2fbackend/main.py",
    "/../backend/main.py",
])
def test_ac_p4_no_traversal_under_prefix(client, path):
    r = client.get(BASE + path)
    assert r.status_code in (400, 404)
    assert "FastAPI" not in r.text and "root:" not in r.text
