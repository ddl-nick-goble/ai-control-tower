"""Live Domino adapter.

Endpoints used (all Public API, bearer auth):
  GET  /api/apps/v1/apps?configurationType=AISYSTEM   deployed Agents (Apps of type AISYSTEM)
  GET  /api/apps/v1/apps/{id}/versions                agent versions
  GET  /api/2.0/mlflow/experiments/get-by-name        MLflow experiment agent_experiment_<app id>
  POST /api/2.0/mlflow/runs/search                    runs of that experiment
  GET  /api/audittrail/v1/auditevents                 access events (Audit Trail API)
There is no separate /api/agents route in this deployment (404); agents are Apps with
configurationType AISYSTEM. Sub-calls degrade to [] / {} with a log line; the agent listing raises so intake reports it.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from backend import demo
from backend.settings import get_setting

from .base import AdapterBase, SourceAgent, SourceRun

log = logging.getLogger(__name__)
LIST_KEYS = ("items", "agents", "apps", "data", "results")
PAGE = 100


def _iso(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, timezone.utc).isoformat()
    return v


class DominoAdapter(AdapterBase):
    platform = "domino"

    # -- http ---------------------------------------------------------------
    def _base_headers(self):
        proxy = os.environ.get("DOMINO_API_PROXY")
        if proxy:
            return proxy.rstrip("/"), {}
        host = os.environ.get("DOMINO_API_HOST")
        if not host:
            raise RuntimeError("DOMINO_API_HOST not set")
        if not getattr(self, "_token", None):  # cached for the life of this adapter (one run)
            self._token = requests.get("http://localhost:8899/access-token", timeout=5).text.strip()
        return host.rstrip("/"), {"Authorization": f"Bearer {self._token}"}

    def _call(self, method, path, params=None, body=None, strict=False):
        """Return parsed JSON or None on failure; with strict=True raise RuntimeError instead."""
        try:
            base, headers = self._base_headers()
            kw = {"headers": headers, "params": params, "timeout": 20}
            if body is not None:
                kw["json"] = body
            def send():
                if method == "GET":
                    return requests.get(base + path, **kw)
                return requests.request(method, base + path, **kw)
            resp = send()
            code = getattr(resp, "status_code", 200)
            if code == 401 and not os.environ.get("DOMINO_API_PROXY"):
                self._token = None  # cached token may have expired: re-fetch once
                base, headers = self._base_headers()
                kw["headers"] = headers
                resp = send()
                code = getattr(resp, "status_code", 200)
            if code >= 400:
                log.warning("domino %s %s -> HTTP %s", method, path, code)
                if strict:
                    raise RuntimeError(f"domino {method} {path} -> HTTP {code}")
                return None
            return resp.json()
        except Exception as e:  # endpoint unavailable: degrade gracefully unless strict
            if strict:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"domino {method} {path} unavailable: {e}") from e
            log.warning("domino %s %s unavailable: %s", method, path, e)
            return None

    @staticmethod
    def _items(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for k in LIST_KEYS:
                if isinstance(payload.get(k), list):
                    return payload[k]
        return []

    # -- agents -------------------------------------------------------------
    @staticmethod
    def _item_pid(it):
        proj = it.get("project")
        return it.get("projectId") or (proj.get("id") if isinstance(proj, dict) else None)

    def _fetch_apps(self, project_id=None):
        seen, out = set(), []
        types = ["AISYSTEM"] + (["STANDARD"] if os.environ.get("ACT_DOMINO_INCLUDE_APPS") == "1" else [])
        cap = int(os.environ.get("ACT_DOMINO_MAX_AGENTS", "25"))
        pid = None
        if project_id:
            pid = project_id
        elif os.environ.get("ACT_DOMINO_SCOPE") != "all":
            pid = os.environ.get("ACT_DOMINO_PROJECT_ID") or os.environ.get("DOMINO_PROJECT_ID")
            if not pid:
                raise RuntimeError("domino adapter: no project id (set ACT_DOMINO_PROJECT_ID or DOMINO_PROJECT_ID, "
                                   "or ACT_DOMINO_SCOPE=all)")
        for ctype in types:
            offset = 0
            while len(out) < cap:
                params = {"configurationType": ctype, "limit": PAGE, "offset": offset}
                if pid:
                    params["projectId"] = pid
                payload = self._call("GET", "/api/apps/v1/apps", params, strict=True)
                items = self._items(payload)
                # Ownership: item["project"]["id"] in the real apps API (top-level projectId also accepted).
                pids = [self._item_pid(it) for it in items if isinstance(it, dict)]
                exposes = any(pids)  # page carries project ids: require confirmation per item
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    ipid = self._item_pid(it)
                    if pid and ((exposes and not ipid) or (ipid and str(ipid) != str(pid))):
                        continue
                    nid = it.get("id") or it.get("agentId") or it.get("appId")
                    if nid and nid not in seen:
                        seen.add(nid)
                        out.append(it)
                total = ((payload or {}).get("metadata") or {}).get("totalCount") if isinstance(payload, dict) else None
                offset += PAGE
                if len(items) < PAGE or total is None or offset >= total:
                    break
        return out[:cap]

    def _versions(self, nid):
        vs = []
        for v in self._items(self._call("GET", f"/api/apps/v1/apps/{nid}/versions", {"limit": 20})):
            if isinstance(v, dict) and v.get("versionNumber") is not None:
                vs.append({"version": str(v["versionNumber"]), "created": _iso(v.get("createdAt")),
                           "commit": ((v.get("currentInstance") or {}).get("gitCommitId"))})
        return vs

    def _experiment(self, nid):
        d = self._call("GET", "/api/2.0/mlflow/experiments/get-by-name",
                       {"experiment_name": f"agent_experiment_{nid}"})
        exp = d.get("experiment") if isinstance(d, dict) else None
        return exp if isinstance(exp, dict) else None

    @staticmethod
    def _identity(principal):
        allow = {x.strip() for x in (get_setting("service_accounts") or "").split(",") if x.strip()}
        principal = principal or "unknown"
        if principal in allow:
            return {"type": "domino_service_account", "principal": principal, "distinct": True}
        return {"type": "domino_app_run_as_publisher", "principal": principal, "distinct": False}

    def _to_agent(self, it):
        nid = it.get("id") or it.get("agentId") or it.get("appId")
        cur = it.get("currentVersion") or it.get("latestVersion") or {}
        inst = cur.get("currentInstance") or {}
        owner = (it.get("ownerName") or it.get("owner") or (it.get("project") or {}).get("ownerUsername")
                 or (inst.get("publisher") or {}).get("name"))
        if isinstance(owner, dict):
            owner = owner.get("name") or owner.get("userName")
        versions = self._versions(nid)
        if not versions and cur.get("versionNumber") is not None:
            versions = [{"version": str(cur["versionNumber"]), "created": _iso(cur.get("createdAt"))}]
        exp = self._experiment(nid)
        publisher = (inst.get("publisher") or {}).get("name") or owner
        return SourceAgent(
            platform="domino", native_id=str(nid), name=it.get("name") or str(nid),
            description=it.get("description") or "",
            version=versions[0]["version"] if versions else str(cur.get("versionNumber") or "1"),
            owner=owner, status=str(it.get("status") or inst.get("status") or ""),
            last_updated=_iso(it.get("updatedAt") or it.get("lastUpdated")), url=it.get("url"),
            provider="Domino", versions=versions,
            identity=self._identity(publisher),
            extra={"project": (it.get("project") or {}).get("name"),
                   "experiment_id": (exp or {}).get("experiment_id"),
                   "experiment_name": (exp or {}).get("name"),
                   "configuration_type": it.get("configurationType")},
            raw=it)

    # -- locally hosted agents (stand-in until the agent is deployed as a Domino Agent) --------------------
    _VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-((?:[^{}]|\{[^{}]*\})*)\}")

    @classmethod
    def _expand(cls, v):
        if isinstance(v, str):
            prev = None
            while prev != v:  # innermost-first, nested defaults allowed
                prev, v = v, cls._VAR.sub(lambda m: os.environ.get(m.group(1)) or m.group(2), v)
            return v
        if isinstance(v, list):
            return [cls._expand(x) for x in v]
        if isinstance(v, dict):
            return {k: cls._expand(x) for k, x in v.items()}
        return v

    def _local_agents(self):
        path = (os.environ.get("ACT_DOMINO_LOCAL_AGENTS") or "").strip()
        if not path:
            return []
        try:
            entries = self._expand(json.loads(Path(path).read_text())).get("agents", [])
            if not isinstance(entries, list) or not all(isinstance(e, dict) and e.get("native_id") and e.get("name")
                                                        for e in entries):
                raise ValueError("'agents' must be a list of entries with native_id and name")
        except (OSError, ValueError, AttributeError) as e:
            log.warning("ACT_DOMINO_LOCAL_AGENTS=%s ignored, no local agents added: %s", path, e)
            return []
        out = []
        for e in entries:
            principal = e.get("principal") or e.get("owner")
            allow = {x.strip() for x in (get_setting("service_accounts") or "").split(",") if x.strip()}
            if demo.enabled() and e["native_id"] == (os.environ.get("RECON_AGENT_ID") or "local-recon-agent"):
                ident = demo.service_account_identity()  # labelled stand-in: no real service account in demo mode
            else:
                ident = (self._identity(principal) if principal in allow else
                         {"type": "domino_workspace_user", "principal": principal or "unknown", "distinct": False})
            out.append(SourceAgent(
                platform="domino", native_id=e["native_id"], name=e["name"], description=e.get("description", ""),
                version=str(e.get("version", "1")), owner=e.get("owner"), status=e.get("status", ""),
                provider="Domino", tools=list(e.get("tools", [])), data_scopes=list(e.get("data_scopes", [])),
                systems=list(e.get("systems", [])), identity=ident,
                dependencies=e.get("dependencies") or {},
                extra={"hosting": "workspace (not deployed)", "local_agents_file": os.path.basename(path)},
                raw=e))
        return out

    def list_agents(self):
        # demo mode: no live Domino listing, only the locally hosted agents below
        agents = [] if demo.enabled() else [self._to_agent(it) for it in self._fetch_apps()]  # errors propagate to intake
        have = {a.native_id for a in agents}
        return agents + [a for a in self._local_agents() if a.native_id not in have]

    # -- connections contract -------------------------------------------------------------------------
    @staticmethod
    def _current_project():
        return os.environ.get("ACT_DOMINO_PROJECT_ID") or os.environ.get("DOMINO_PROJECT_ID")

    def auth_methods(self):
        return ["none"]  # the Domino API proxy adds the token

    def test_connection(self, cfg, creds):
        name, detail = "Domino API reachable", "the Domino API proxy answers"
        if not (os.environ.get("DOMINO_API_PROXY") or os.environ.get("DOMINO_API_HOST")):
            return [{"name": name, "detail": "neither DOMINO_API_PROXY nor DOMINO_API_HOST is set", "required": True,
                     "ok": False, "fix": "ACT is not running inside Domino. Run it as a Domino App or Workspace so "
                                         "Domino's API proxy is available."}]
        pid = self._current_project()
        path = f"/api/projects/v1/projects/{pid}" if pid else "/api/projects/v1/projects"
        try:
            self._call("GET", path, None if pid else {"limit": 1}, strict=True)
        except Exception as e:  # never raises: the checklist reports it
            return [{"name": name, "detail": f"Domino API did not answer: {e}", "required": True, "ok": False,
                     "fix": "Domino's API proxy did not answer. Restart the ACT app, and check that the project "
                            "still exists and the app's owner can read it."}]
        return [{"name": name, "detail": detail, "required": True, "ok": True, "fix": None}]

    def list_scopes(self, cfg, creds):
        pid = self._current_project()
        if not pid:
            raise RuntimeError("domino adapter: no project id (set DOMINO_PROJECT_ID)")
        proj = self._call("GET", f"/api/projects/v1/projects/{pid}")
        label = (proj or {}).get("name") if isinstance(proj, dict) else None
        return [{"id": f"domino:project:{pid}", "label": label or pid, "selected": True, "children": []}]

    def discover(self, cfg, creds, scopes, progress=None):
        pids = []
        for s in scopes or []:
            if str(s).startswith("domino:project:") and str(s)[15:] not in pids:
                pids.append(str(s)[15:])
        if not pids:
            pids = [None]  # default scope: the current project (or ACT_DOMINO_SCOPE=all)
        agents, seen = [], set()
        if not demo.enabled():
            for i, pid in enumerate(pids):
                for it in self._fetch_apps(pid):
                    a = self._to_agent(it)
                    if a.native_id not in seen:
                        seen.add(a.native_id)
                        agents.append(a)
                if progress:
                    progress(i + 1, len(pids), f"Read Domino project {pid or 'current'}")
        elif progress:
            progress(len(pids), len(pids), "Demo mode: no live listing")
        return agents + [a for a in self._local_agents() if a.native_id not in seen]

    def get_agent(self, native_id):
        if demo.enabled():
            return super().get_agent(native_id)
        it = self._call("GET", f"/api/apps/v1/apps/{native_id}")
        if isinstance(it, dict) and (it.get("id") or it.get("name")):
            return self._to_agent(it)
        return super().get_agent(native_id)

    # -- runs and access events -----------------------------------------------
    def list_runs(self, native_id, since=None):
        if demo.enabled():
            return []
        exp = self._experiment(native_id)
        if not exp:
            return []
        body = {"experiment_ids": [exp["experiment_id"]], "max_results": 200,
                "order_by": ["attributes.start_time DESC"]}
        if since:
            ms = int(since.timestamp() * 1000) if hasattr(since, "timestamp") else int(since)
            body["filter"] = f"attributes.start_time > {ms}"
        d = self._call("POST", "/api/2.0/mlflow/runs/search", body=body)
        runs = (d or {}).get("runs") or [] if isinstance(d, dict) else []
        return [SourceRun(native_id=native_id, run_id=r["info"]["run_id"], started_at=_iso(r["info"].get("start_time")),
                          status=r["info"].get("status"), raw=r) for r in runs if isinstance(r, dict) and "info" in r]

    def access_events(self, native_id, since=None, limit=100):
        """Audit Trail events touching this agent (client-side filter on entity id)."""
        if demo.enabled():
            return []
        start = int(since.timestamp() * 1000) if hasattr(since, "timestamp") else int(since or (time.time() - 86400) * 1000)
        d = self._call("GET", "/api/audittrail/v1/auditevents", {"startTimestamp": start, "limit": limit})
        evs = (d or {}).get("events") or [] if isinstance(d, dict) else []
        return [e for e in evs if native_id in str(e)]
