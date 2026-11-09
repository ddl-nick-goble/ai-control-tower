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
