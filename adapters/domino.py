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
import logging
import os
import time
from datetime import datetime, timezone

import requests

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
            if method == "GET":
                resp = requests.get(base + path, **kw)
            else:
                resp = requests.request(method, base + path, **kw)
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
    def _fetch_apps(self):
        seen, out = set(), []
        types = ["AISYSTEM"] + (["STANDARD"] if os.environ.get("ACT_DOMINO_INCLUDE_APPS") == "1" else [])
        cap = int(os.environ.get("ACT_DOMINO_MAX_AGENTS", "25"))
        pid = None
        if os.environ.get("ACT_DOMINO_SCOPE") != "all":
            pid = os.environ.get("ACT_DOMINO_PROJECT_ID") or os.environ.get("DOMINO_PROJECT_ID")
        for ctype in types:
            offset = 0
            while len(out) < cap:
                params = {"configurationType": ctype, "limit": PAGE, "offset": offset}
                if pid:
                    params["projectId"] = pid
                payload = self._call("GET", "/api/apps/v1/apps", params, strict=True)
                items = self._items(payload)
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    ipid = it.get("projectId") or (it.get("project") or {}).get("id")
                    if pid and ipid and str(ipid) != str(pid):  # API may ignore the filter
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
        allow = {x.strip() for x in os.environ.get("ACT_SERVICE_ACCOUNTS", "").split(",") if x.strip()}
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

    def list_agents(self):
        return [self._to_agent(it) for it in self._fetch_apps()]  # listing errors propagate to intake

    def get_agent(self, native_id):
        it = self._call("GET", f"/api/apps/v1/apps/{native_id}")
        if isinstance(it, dict) and (it.get("id") or it.get("name")):
            return self._to_agent(it)
        return super().get_agent(native_id)

    # -- runs and access events -----------------------------------------------
    def list_runs(self, native_id, since=None):
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
        start = int(since.timestamp() * 1000) if hasattr(since, "timestamp") else int(since or (time.time() - 86400) * 1000)
        d = self._call("GET", "/api/audittrail/v1/auditevents", {"startTimestamp": start, "limit": limit})
        evs = (d or {}).get("events") or [] if isinstance(d, dict) else []
        return [e for e in evs if native_id in str(e)]
