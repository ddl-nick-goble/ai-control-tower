"""Adapter contract and shared helpers.

Replay adapters read fixtures; the real HTTP call is written but only used when
ACT_LIVE_<PLATFORM>=1 (untested live). The Domino adapter is always live.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SourceAgent:
    platform: str
    native_id: str
    name: str
    description: str = ""
    version: str = "1"
    owner: Optional[str] = None
    status: str = ""
    last_updated: Optional[str] = None
    url: Optional[str] = None
    provider: Optional[str] = None
    tools: list = field(default_factory=list)
    data_scopes: list = field(default_factory=list)
    systems: list = field(default_factory=list)
    value_limits: dict = field(default_factory=dict)   # {tool: {"amount": n, "currency": "USD"}}
    criteria: dict = field(default_factory=dict)        # raw source-side criteria (autonomy, consequence, ...)
    identity: dict = field(default_factory=dict)        # {"type", "principal", "distinct", ...}
    versions: list = field(default_factory=list)        # [{"version", "created"}]
    dependencies: dict = field(default_factory=dict)    # {"models": [...], "mcp_servers": [...]}
    extra: dict = field(default_factory=dict)
    raw: Any = None


@dataclass
class SourceRun:
    native_id: str
    run_id: str
    started_at: Optional[str] = None
    status: Optional[str] = None
    raw: Any = None


class AdapterBase:
    platform = ""

    def list_agents(self) -> list:
        raise NotImplementedError

    def get_agent(self, native_id: str) -> SourceAgent:
        for a in self.list_agents():
            if a.native_id == native_id:
                return a
        raise KeyError(native_id)

    def list_runs(self, native_id: str, since=None) -> list:
        return []

    # ---- Connections contract (PO-3 implements these per provider) ----
    # `cfg` is the connection's config dict; `creds` is what backend.connections.federation.exchange() returned
    # (a fresh provider credential, never stored).
    def auth_methods(self) -> list:
        """The ways ACT can authenticate to this provider, e.g. ["workload_identity"]."""
        return ["workload_identity"]

    def test_connection(self, cfg: dict, creds: dict) -> list:
        """-> [{name, detail, required, ok, fix}], one item per permission or reachability check."""
        return [{"name": "Provider access check", "detail": "", "required": True, "ok": False,
                 "fix": f"Live access checks for {self.platform or 'this provider'} are not available yet."}]

    def list_scopes(self, cfg: dict, creds: dict) -> list:
        """-> scope tree [{id, name, kind, children: [...]}] (accounts, projects, workspaces...)."""
        raise NotImplementedError

    def coverage(self, cfg: dict, creds: dict, scopes=None) -> list:
        """-> [{reason, fix}] for agents ACT may be missing. Default: nothing to report."""
        return []

    def discover(self, cfg: dict, creds: dict, scopes: list, progress=None) -> list:
        """-> [SourceAgent] found in the selected scope ids. `progress(done, total, message)` is optional."""
        raise NotImplementedError


def fixtures_dir() -> Path:
    return Path(os.environ.get("ACT_FIXTURES_DIR") or ROOT / "fixtures")


def load_fixture(rel: str):
    return json.loads((fixtures_dir() / rel).read_text())


def live_enabled(platform: str) -> bool:
    return os.environ.get(f"ACT_LIVE_{platform.upper()}") == "1"


def http_get_json(platform: str, path: str, params=None):
    """Real call for a hyperscaler API (untested live). Base URL and bearer token come from
    ACT_<PLATFORM>_URL / ACT_<PLATFORM>_TOKEN."""
    import requests
    p = platform.upper()
    base = os.environ[f"ACT_{p}_URL"].rstrip("/")
    resp = requests.get(base + path, params=params, timeout=30,
                        headers={"Authorization": f"Bearer {os.environ.get(f'ACT_{p}_TOKEN', '')}"})
    resp.raise_for_status()
    return resp.json()
