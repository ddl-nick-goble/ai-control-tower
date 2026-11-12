import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    path = tmp_path / "act.db"
    monkeypatch.setenv("ACT_DB", str(path))
    monkeypatch.setenv("ACT_DB_PREWARM_COUNT", "0")
    for k in ("DOMINO_IS_GIT_BASED", "DOMINO_PROJECT_NAME", "DOMINO_API_PROXY"):
        monkeypatch.delenv(k, raising=False)
    return path


@pytest.fixture
def db_mod(db_env):
    """Import backend.db fresh so it picks up env."""
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    return importlib.import_module("backend.db")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs a live deployment; skipped unless its env var is set")
    config.addinivalue_line("markers", "default_hosts: run with ACT_EXTERNAL_ALLOW_HOSTS unset")


@pytest.fixture(autouse=True)
def _hermetic_workspace_owner_mode(monkeypatch):
    """The test host may itself be a Domino Workspace (DOMINO_RUN_HOST_PATH=/notebookSession/...), which would
    switch SR-2 workspace owner mode on and turn anonymous 401s into owner calls. Pin it off by default;
    tests that exercise owner mode set ACT_WORKSPACE_OWNER_MODE (or delenv it for auto mode) themselves."""
    monkeypatch.setenv("ACT_WORKSPACE_OWNER_MODE", "0")
