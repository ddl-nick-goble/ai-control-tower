"""T-17: App Preview runtime: committed dist, prefix-agnostic serving, missing-dist page."""
import importlib
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT

INDEX_HTML = ('<!doctype html><html><body><div id="root"></div>'
              '<script type="module" src="./assets/t17.js"></script></body></html>')
ASSET_JS = "console.log('t17-asset');"
PREVIEW = "/preview/draft123"
APPS = "/apps/app456"


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _tracked(path):
    r = _git("ls-files", path)
    return [l for l in r.stdout.splitlines() if l]


def _asset_refs(html):
    refs = re.findall(r'(?:src|href)="([^"]*assets/[^"]+)"', html)
    return [re.sub(r"^(\./|/)", "", r.split("?")[0]) for r in refs]


def _load_app(monkeypatch, tmp_path, with_dist, host_path="/some/other/r/notebookSession/zzz/"):
    """Import a copy of backend/ rooted in tmp_path so the real frontend/dist is never touched."""
    root = tmp_path / "root"
    root.mkdir()
    for entry in ROOT.iterdir():  # share everything except backend (copied) and frontend (stubbed)
        if entry.name in ("backend", "frontend", "tests", ".git", ".worktrees", "node_modules"):
            continue
        (root / entry.name).symlink_to(entry)
    shutil.copytree(ROOT / "backend", root / "backend",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if with_dist:
        d = root / "frontend" / "dist"
        (d / "assets").mkdir(parents=True)
        (d / "index.html").write_text(INDEX_HTML)
        (d / "assets" / "t17.js").write_text(ASSET_JS)
    monkeypatch.setenv("DOMINO_RUN_HOST_PATH", host_path)
    monkeypatch.setenv("ACT_WORKSPACE_OWNER_MODE", "0")  # these tests cover prefix handling and the write guard, not SR-2
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    monkeypatch.syspath_prepend(str(root))
    main = importlib.import_module("backend.main")
    assert str(root) in os.path.abspath(main.__file__)
    return main


@pytest.fixture
def client(db_env, monkeypatch, tmp_path):
    main = _load_app(monkeypatch, tmp_path, with_dist=True)
    with TestClient(main.app) as c:
        yield c
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


@pytest.fixture
def client_nodist(db_env, monkeypatch, tmp_path):
    main = _load_app(monkeypatch, tmp_path, with_dist=False)
    with TestClient(main.app) as c:
        yield c
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


# ---- F-1 ----
def test_f1_dist_index_is_tracked():
    assert "frontend/dist/index.html" in _tracked("frontend/dist")


def test_f1_referenced_assets_are_tracked():
    tracked = set(_tracked("frontend/dist"))
    assert "frontend/dist/index.html" in tracked, "index.html not committed"
    html = (ROOT / "frontend" / "dist" / "index.html").read_text()
    refs = _asset_refs(html)
    assert refs, "index.html references no assets/ files"
    missing = [r for r in refs if f"frontend/dist/{r}" not in tracked]
    assert not missing, f"assets not tracked by git: {missing}"


# ---- F-2 ----
def _last_commit(paths):
    r = _git("log", "-1", "--format=%ct", "--", *paths)
    return int(r.stdout.strip() or 0)


def test_f2_dist_not_older_than_sources():
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone: git history unreliable for comparing commit times")
    dist_t = _last_commit(["frontend/dist"])
    assert dist_t, "frontend/dist has no commits"
    src_t = _last_commit(["frontend/src", "frontend/index.html", "vite.config.js", "package.json"])
    assert src_t <= dist_t, "frontend sources changed after the committed dist was built"


# ---- F-3 ----
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.read().decode("utf-8", "replace")


def test_f3_app_sh_serves_ui_without_npm(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("bash", "dirname", "find", "env"):
        p = shutil.which(name)
        if p:
            (bindir / name).symlink_to(p)
    (bindir / "python3").symlink_to(sys.executable)
    env = {k: v for k, v in os.environ.items() if k != "DOMINO_RUN_HOST_PATH"}
    port = _free_port()
    env.update(PATH=str(bindir), PORT=str(port), ACT_DB=str(tmp_path / "act.db"),
               ACT_DB_PREWARM_COUNT="0", ACT_LLM_PROVIDER="mock")
    env["PYTHONPATH"] = str(ROOT)
    assert shutil.which("npm", path=env["PATH"]) is None
    proc = subprocess.Popen([str(bindir / "bash"), str(ROOT / "app.sh")], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                _get(base + "/api/health")
                break
            except Exception:
                if proc.poll() is not None:
                    pytest.fail("app.sh exited before serving")
                time.sleep(0.5)
        else:
            pytest.fail("app did not become ready")
        status, body = _get(base + "/")
        assert status == 200
        assert '<div id="root">' in body
        js = [r for r in _asset_refs(body) if r.endswith(".js")]
        assert js, "index.html references no JS asset"
        st, _ = _get(f"{base}/{js[0]}")
        assert st == 200
    finally:
        proc.kill()
        proc.wait(timeout=10)


# ---- F-4 ----
@pytest.mark.parametrize("prefix", [PREVIEW, APPS])
def test_f4_prefixed_index(client, prefix):
    r = client.get(f"{prefix}/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.text == client.get("/").text
    assert "t17.js" in r.text


def test_f4_preview_api_health(client):
    r = client.get(f"{PREVIEW}/api/health")
    assert r.status_code == 200
    assert r.json() == client.get("/api/health").json()


def test_f4_apps_api_health(client):
    r = client.get(f"{APPS}/api/health")
    assert r.status_code == 200
    assert r.json() == client.get("/api/health").json()


def test_f4_preview_asset(client):
    r = client.get(f"{PREVIEW}/assets/t17.js")
    assert r.status_code == 200
    assert r.text == ASSET_JS


@pytest.mark.parametrize("prefix", [PREVIEW, APPS])
def test_f4_no_trailing_slash_redirects(client, prefix):
    r = client.get(prefix, follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].endswith(f"{prefix}/")


def test_f4_unknown_paths_404(client):
    assert client.get(f"{PREVIEW}/api/does-not-exist").status_code == 404
    assert client.get(f"{PREVIEW}/assets/nope.js").status_code == 404
    assert client.get("/assets/nope.js").status_code == 404
    assert client.get("/api/does-not-exist").status_code == 404


# ---- F-5 ----
def test_f5_missing_dist_gives_readable_page(client_nodist):
    r = client_nodist.get("/")
    assert r.status_code in (200, 503)
    assert "text/html" in r.headers["content-type"]
    low = r.text.lower()
    assert "frontend" in low and "build" in low and "missing" in low
    assert "npm" in low
    assert r.text.strip() != '{"detail":"Not Found"}'


def test_f5_missing_dist_health_still_works(client_nodist):
    r = client_nodist.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# ---- F-6 ----
def test_f6_t16_suite_green():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x",
                        str(ROOT / "tests" / "test_t16_proxy_prefix.py")],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1500:]


def test_f6_unprefixed_and_workspace_prefix(db_env, monkeypatch, tmp_path):
    run = "/u/p/r/notebookSession/run1/"
    main = _load_app(monkeypatch, tmp_path, with_dist=True, host_path=run)
    with TestClient(main.app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/api/health").status_code == 200
        assert c.get(run.rstrip("/") + "/proxy/9888/api/health").status_code == 200
        assert c.get(run.rstrip("/") + "/proxy/9888/").status_code == 200
