"""Databricks provider adapter.

Databricks has no single "list agents" API, so discover() unions five sources per workspace:
  serving endpoints (agent task types)   GET /api/2.0/serving-endpoints, /{name}
  Apps                                   GET /api/2.0/apps, /{name}/deployments
  Agent Bricks (Beta)                    GET /api/2.1/knowledge-assistants, /api/2.1/supervisor-agents, /{id}/tools
  UC agent services (Beta)               GET /api/2.1/unity-catalog/agent-services, /connections/{name}
  UC models and functions                GET /api/2.1/unity-catalog/models/{full}/versions, /functions
Account workspaces come from GET /api/2.0/accounts/{id}/workspaces on the accounts host.
Every network call goes through the module-level `_http`. Each source degrades alone (403 / 404 / network error
gives nothing for that source). Unknowns stay "unknown" or empty; env values and function bodies are never stored.
Sample mode (cfg["sample"]) makes no HTTP call. list_agents() is the demo replay (fixtures/databricks/).
"""
import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

from .base import AdapterBase, SourceAgent, live_enabled, load_fixture

ACCOUNT_HOST = "https://accounts.cloud.databricks.com"
MAX_PAGES = 50
UNKNOWN = "unknown"

FIX_SERVING = ("Grant CAN_VIEW on serving endpoints to the group act-readers: databricks permissions update "
               "serving-endpoints <endpoint-id> --json '{\"access_control_list\":[{\"group_name\":\"act-readers\","
               "\"permission_level\":\"CAN_VIEW\"}]}'. New endpoints stay invisible until they are granted too.")
FIX_APPS = ("Grant CAN_USE on the apps ACT should list to the group act-readers: databricks permissions update apps "
            "<app-name> --json '{\"access_control_list\":[{\"group_name\":\"act-readers\",\"permission_level\":\"CAN_USE\"}]}'.")
FIX_UC = ("Grant BROWSE on catalog <catalog> to act-readers so ACT can read data scopes (no data access): "
          "GRANT BROWSE ON CATALOG <catalog> TO `act-readers`.")
FIX_SERVICES = ("Agent services (Beta) aren't enabled. Optional: an account admin turns on the Unity Gateway beta in "
                "Account console > Previews, then grants EXECUTE or READ METADATA on the services to act-readers.")
FIX_BRICKS = ("Agent Bricks isn't available in this workspace or region, so Knowledge Assistants and Supervisor Agents "
              "were skipped. Nothing to fix unless you use them; then ask a workspace admin to enable Agent Bricks.")
FIX_SIGNIN = ("Databricks didn't accept ACT's sign-in. Re-run the setup script, or assign the service principal "
              "act-reader to this workspace.")
FIX_NO_WS = "No workspace host is known. Re-run the setup script and paste the new connection code."
FIX_WRITE = ("ACT only reads. A principal with CAN_MANAGE or CAN_QUERY can also change or invoke the endpoint. "
             "Replace it with a read-only grant (CAN_VIEW) for act-readers.")
WRITE_LEVELS = {"CAN_MANAGE", "CAN_QUERY", "CAN_RUN", "IS_OWNER"}
READER_GROUP = "act-readers"


# ---------------------------------------------------------------- http
def _http(method, url, **kw):
    """-> (status, headers, json or None). The only place this module touches the network."""
    import requests
    kw.setdefault("timeout", 30)
    resp = requests.request(method, url, **kw)
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, dict(resp.headers or {}), body


def _iso_ms(v):
    return datetime.fromtimestamp(v / 1000, timezone.utc).isoformat() if isinstance(v, (int, float)) else v


def _short(name):
    return str(name).rsplit(".", 1)[-1]


class DatabricksAdapter(AdapterBase):
    platform = "databricks"

    # ------------------------------------------------------------ request helpers
    @staticmethod
    def _sample(cfg):
        return bool((cfg or {}).get("sample"))

    @staticmethod
    def _get(host, path, creds, params=None):
        """-> (status, body). Network errors give (0, None); a 429 is retried twice."""
        kw = {"headers": {"Authorization": f"Bearer {(creds or {}).get('token', '')}"}}
        if params:
            kw["params"] = params
        url = host.rstrip("/") + path
        for attempt in range(3):
            try:
                status, headers, body = _http("GET", url, **kw)
            except Exception:
                return 0, None
            if status == 429 and attempt < 2:
                try:
                    wait = float((headers or {}).get("Retry-After") or 1)
                except (TypeError, ValueError):
                    wait = 1
                time.sleep(min(wait, 5))
                continue
            return status, body
        return 0, None

    def _paged(self, host, path, key, creds, params=None):
        """All items of a list endpoint, following next_page_token. -> (status of the first page, items)."""
        items, seen, token, first = [], set(), None, None
        for _ in range(MAX_PAGES):
            p = dict(params or {})
            if token:
                p["page_token"] = token
            status, body = self._get(host, path, creds, p or None)
            first = status if first is None else first
            if status != 200 or not isinstance(body, dict):
                break
            page = [x for x in (body.get(key) or []) if isinstance(x, dict)]
            ident = [json.dumps(x, sort_keys=True, default=str) for x in page]
            fresh = [x for x, i in zip(page, ident) if i not in seen]
            seen.update(ident)
            items += fresh
            token = body.get("next_page_token")
            if not token or not fresh:
                break
        return first, items

    def _workspace_hosts(self, cfg, creds):
        hosts = {str(w["id"]): w.get("host") for w in (cfg.get("workspaces") or []) if w.get("id") and w.get("host")}
        return hosts

    def _account_workspaces(self, cfg, creds):
        acct = cfg.get("account_id")
        if not acct:
            return None
        status, body = self._get(cfg.get("account_host") or ACCOUNT_HOST, f"/api/2.0/accounts/{acct}/workspaces", creds)
        if status == 200 and isinstance(body, list):
            return [w for w in body if isinstance(w, dict)]
        return None

    # ------------------------------------------------------------ contract: auth + scopes
    def auth_methods(self):
        return ["federated_service_principal", "oauth_user_sign_in", "oauth_m2m_client_secret"]

    def list_scopes(self, cfg, creds):
        cfg = cfg or {}
        if self._sample(cfg):
            from backend.connections import sample
            t = sample.tenant("databricks")

            def sel(n):
                return {"id": n["id"], "label": n["label"], "selected": True,
                        "children": [sel(c) for c in n.get("children") or []]}
            return [{"id": "dbx:account:sample", "label": t.get("label") or "Databricks account", "selected": True,
                     "children": [sel(n) for n in t["tree"]]}]
        listed = self._account_workspaces(cfg, creds)
        leaves = []
        if listed is not None:
            for w in listed:
                wid = str(w.get("workspace_id"))
                dep = w.get("deployment_name")
                host = (cfg.get("workspaces") and next((x.get("host") for x in cfg["workspaces"] if str(x.get("id")) == wid), None)) \
                    or (f"https://{dep}.cloud.databricks.com" if dep else None)
                leaves.append({"id": f"dbx:ws:{wid}", "label": w.get("workspace_name") or wid, "selected": True,
                               "children": [], "host": host})
        else:  # not an account admin (403): use the workspaces from the setup code
            for w in cfg.get("workspaces") or []:
                wid = str(w.get("id"))
                leaves.append({"id": f"dbx:ws:{wid}", "label": w.get("name") or wid, "selected": True,
                               "children": [], "host": w.get("host")})
        acct = cfg.get("account_id") or UNKNOWN
        return [{"id": f"dbx:account:{acct}", "label": f"Databricks account {str(acct)[:8]}", "selected": True,
                 "children": leaves}]

    def _selected_workspaces(self, cfg, creds, scopes):
        """[(workspace id, host)] for the selected scope ids (all configured workspaces when none are given)."""
        hosts = self._workspace_hosts(cfg, creds)
        ids = []
        for s in scopes or []:
            parts = str(s).split(":")
            if len(parts) >= 3 and parts[0] == "dbx" and parts[1] == "ws" and parts[2] not in ids:
                ids.append(parts[2])
        if not ids:
            ids = list(hosts)
        if any(i not in hosts for i in ids):
            for w in self._account_workspaces(cfg, creds) or []:
                dep = w.get("deployment_name")
                if dep:
                    hosts.setdefault(str(w.get("workspace_id")), f"https://{dep}.cloud.databricks.com")
        return [(i, hosts[i]) for i in ids if hosts.get(i)]

    # ------------------------------------------------------------ probes (test_connection + coverage)
    def _probe(self, cfg, creds):
        """Run the permission checks against the first workspace. -> dict of results, or None without a host."""
        ws = self._workspace_hosts(cfg, creds)
        if not ws:
            return None
        wid, host = next(iter(ws.items()))
        r = {"wid": wid, "host": host}
        r["me"], me = self._get(host, "/api/2.0/preview/scim/v2/Me", creds)
        r["me_name"] = (me or {}).get("userName") if isinstance(me, dict) else None
        r["serving"], eps = self._get(host, "/api/2.0/serving-endpoints", creds)
        r["endpoints"] = [e for e in ((eps or {}).get("endpoints") or []) if isinstance(e, dict)] if isinstance(eps, dict) else []
        r["apps"], _ = self._get(host, "/api/2.0/apps", creds, {"page_size": 1})
        r["uc"], _ = self._get(host, "/api/2.1/unity-catalog/catalogs", creds, {"max_results": 1})
        r["services"], _ = self._get(host, "/api/2.1/unity-catalog/agent-services", creds, {"page_size": 1})
        r["bricks"], _ = self._get(host, "/api/2.1/knowledge-assistants", creds, {"page_size": 1})
        r["write"] = []
        agent_eps = [e for e in r["endpoints"] if str(e.get("task") or "").startswith("agent/")
                     or str(e.get("name") or "").startswith("agents_")]
        if r["serving"] == 200 and agent_eps and agent_eps[0].get("id"):
            st, acl = self._get(host, f"/api/2.0/permissions/serving-endpoints/{agent_eps[0]['id']}", creds)
            mine = {READER_GROUP, r["me_name"]} - {None}
            for entry in ((acl or {}).get("access_control_list") or []) if st == 200 and isinstance(acl, dict) else []:
                who = entry.get("group_name") or entry.get("user_name") or entry.get("service_principal_name")
                levels = {p.get("permission_level") for p in entry.get("all_permissions") or []}
                if who in mine and levels & WRITE_LEVELS:
                    r["write"].append((agent_eps[0].get("name"), who, sorted(levels & WRITE_LEVELS)))
        return r

    def test_connection(self, cfg, creds):
        cfg = cfg or {}
        if self._sample(cfg):
            from backend.connections import sample
            return sample.permissions("databricks", recheck=True)
        r = self._probe(cfg, creds)
        if r is None:
            return [{"name": "Databricks workspace", "detail": "no workspace host in the connection", "required": True,
                     "ok": False, "fix": FIX_NO_WS}]

        def item(name, detail, required, status, fix, ok_detail=None):
            ok = status == 200
            return {"name": name, "detail": detail if ok else f"{detail} (HTTP {status or 'no answer'})",
                    "required": required, "ok": ok, "fix": None if ok else fix}
        items = [
            item("Databricks accepted ACT's sign-in", "GET /api/2.0/preview/scim/v2/Me", True, r["me"], FIX_SIGNIN),
            item("Read serving endpoints", "GET /api/2.0/serving-endpoints", True, r["serving"], FIX_SERVING),
            item("Read Apps", "GET /api/2.0/apps", False, r["apps"], FIX_APPS),
            item("Read Unity Catalog metadata", "GET /api/2.1/unity-catalog/catalogs", False, r["uc"], FIX_UC),
            item("Read Agent Bricks (knowledge assistants)", "GET /api/2.1/knowledge-assistants", False, r["bricks"],
                 FIX_BRICKS),
            item("Read agent services (Beta)", "GET /api/2.1/unity-catalog/agent-services", False, r["services"],
                 FIX_SERVICES),
        ]
        for ep, who, levels in r["write"]:
            items.append({"name": "Write access on serving endpoints",
                          "detail": f"{who} holds {', '.join(levels)} on {ep}", "required": False, "ok": False,
                          "fix": FIX_WRITE})
        return items

    def coverage(self, cfg, creds, scopes=None):
        """What discover() will probably miss and how to fix it -> [{reason, fix}]."""
        cfg = cfg or {}
        gaps = []
        if self._sample(cfg):
            return [{"reason": "Endpoints and apps ACT cannot view are invisible, and new ones stay hidden until granted.",
                     "fix": FIX_SERVING}]
        r = self._probe(cfg, creds)
        if r is None:
            return [{"reason": "No workspace host is known, so nothing can be discovered.", "fix": FIX_NO_WS}]
        if r["serving"] != 200:
            gaps.append({"reason": f"Serving endpoints could not be listed (HTTP {r['serving'] or 'no answer'}); "
                                   "endpoint agents are missing.", "fix": FIX_SERVING})
        else:
            gaps.append({"reason": "Only endpoints the ACT service principal can view are listed; endpoints created "
                                   "later stay invisible until granted.",
                         "fix": "Grant CAN_VIEW on new serving endpoints to act-readers, or re-run the setup script "
                                "after creating agents."})
        if r["apps"] != 200:
            gaps.append({"reason": f"Apps could not be listed (HTTP {r['apps'] or 'no answer'}).", "fix": FIX_APPS})
        if r["uc"] != 200:
            gaps.append({"reason": "Unity Catalog metadata is not readable, so tools and data scopes are unknown.",
                         "fix": FIX_UC})
        if r["bricks"] != 200:
            gaps.append({"reason": "Agent Bricks (Knowledge Assistants, Supervisor Agents) are not available.",
                         "fix": FIX_BRICKS})
        if r["services"] != 200:
            gaps.append({"reason": "Agent services (Beta) are not enabled, so external agents registered in Unity "
                                   "Catalog are missing.", "fix": FIX_SERVICES})
        for ep, who, levels in r["write"]:
            gaps.append({"reason": f"{who} holds {', '.join(levels)} on {ep}, which is more than read-only.",
                         "fix": FIX_WRITE})
        return gaps

    # ------------------------------------------------------------ discovery
    def discover(self, cfg, creds, scopes, progress=None):
        cfg = cfg or {}
        if self._sample(cfg):
            return self._sample_agents()
        targets = self._selected_workspaces(cfg, creds, scopes)
        out, total, done = [], len(targets), 0
        ms_cache = {}
        for wid, host in targets:
            if progress:
                progress(done, total, f"Reading workspace {wid}")
            brick_agents, owned = self._agent_bricks(wid, host, creds)
            out += self._endpoints(wid, host, creds, owned)
            out += self._apps(wid, host, creds)
            out += brick_agents
            if "ms" not in ms_cache:  # agent services live in the metastore, list them once
                ms_cache["ms"] = True
                out += self._agent_services(wid, host, creds)
            done += 1
            if progress:
                progress(done, total, f"Finished workspace {wid}")
        return out

    # -- serving endpoints
    @staticmethod
    def _redact_env(detail):
        """Copy of the endpoint detail with every non-{{secrets/..}} env value redacted. -> (copy, keys, plaintext?)"""
        d = copy.deepcopy(detail)
        keys, plain = [], False
        for se in ((d.get("config") or {}).get("served_entities") or []):
            env = se.get("environment_vars")
            if isinstance(env, dict):
                for k, v in list(env.items()):
                    keys.append(k)
                    if not str(v).startswith("{{secrets/"):
                        env[k] = "[redacted]"
                        plain = True
        return d, sorted(set(keys)), plain

    def _endpoints(self, wid, host, creds, owned):
        status, body = self._get(host, "/api/2.0/serving-endpoints", creds)
        if status != 200 or not isinstance(body, dict):
            return []
        out = []
        for item in body.get("endpoints") or []:
            name = item.get("name")
            task = str(item.get("task") or "")
            if not name or task.startswith("llm/"):
                continue
            ents = (item.get("config") or {}).get("served_entities") or []
            if ents and all(e.get("foundation_model") or e.get("external_model") for e in ents):
                continue  # a model, not an agent
            if name in owned:
                continue  # merged into the Knowledge Assistant / Supervisor record
            if task.startswith("agent/"):
                signal = "task"
            elif name.startswith("agents_"):
                signal = "name_prefix"
            else:
                continue
            st, detail = self._get(host, f"/api/2.0/serving-endpoints/{quote(name, safe='')}", creds)
            detail = detail if st == 200 and isinstance(detail, dict) else item
            out.append(self._endpoint_agent(wid, host, creds, detail, signal))
        return out

    def _endpoint_agent(self, wid, host, creds, ep, signal):
        redacted, env_keys, plain = self._redact_env(ep)
        cfgd = ep.get("config") or {}
        ents = cfgd.get("served_entities") or []
        routes = (cfgd.get("traffic_config") or {}).get("routes") or []
        live = None
        if routes:
            top = max(routes, key=lambda r: r.get("traffic_percentage") or 0)
            live = next((e for e in ents if e.get("name") == top.get("served_model_name")), None)
        live = live or next((e for e in ents if e.get("entity_name")), ents[0] if ents else {})
        model, ver = live.get("entity_name"), live.get("entity_version")
        state = ep.get("state") or {}
        if state.get("config_update") == "UPDATE_FAILED":
            status = "UPDATE_FAILED"
        else:
            status = "READY" if state.get("ready") == "READY" else "NOT_READY"
        tags = {t.get("key"): t.get("value") for t in ep.get("tags") or [] if isinstance(t, dict)}
        gw = ep.get("ai_gateway") or {}
        itc = gw.get("inference_table_config") or {}
        table = None
        if itc.get("enabled") and itc.get("table_name_prefix"):
            table = ".".join(x for x in (itc.get("catalog_name"), itc.get("schema_name"),
                                         f"{itc['table_name_prefix']}_payload") if x)
        criteria = {}
        if "rate_limits" in gw:
            criteria["rate_limits"] = gw["rate_limits"]
        if gw.get("guardrails"):
            criteria["guardrails"] = gw["guardrails"]
        versions, tools, scopes, funcs = [], [], [], []
        if model:
            vs = self._model_versions(host, creds, model)
            versions = [{"version": str(v.get("version")), "created": _iso_ms(v.get("created_at"))} for v in vs]
            cur = next((v for v in vs if str(v.get("version")) == str(ver)), vs[0] if vs else None)
            for dep in ((cur or {}).get("model_version_dependencies") or {}).get("dependencies") or []:
                if "function" in dep:
                    funcs.append(dep["function"]["function_full_name"])
                elif "table" in dep:
                    scopes.append(dep["table"]["table_full_name"])
            tools = [_short(f) for f in funcs]
        extra = {"endpoint_name": ep.get("name"), "uc_model": model, "task": ep.get("task"), "agent_signal": signal,
                 "inference_table": table, "workspace_id": wid, "workspace_host": host, "env_var_keys": env_keys}
        if funcs:
            extra["functions"] = self._function_summaries(host, creds, funcs)
        if plain:
            identity = {"type": "secret_in_env", "principal": UNKNOWN, "distinct": False}
        else:
            identity = {"type": "system_sp_passthrough", "principal": ep.get("creator") or UNKNOWN, "distinct": False}
        return SourceAgent(
            platform="databricks", native_id=f"{wid}:endpoint:{ep.get('id')}", name=ep["name"],
            description=ep.get("description") or "", version=str(ver) if ver is not None else UNKNOWN,
            owner=tags.get("owner") or ep.get("creator"), status=status,
            last_updated=_iso_ms(ep.get("last_updated_timestamp")), url=f"{host}/ml/endpoints/{ep['name']}",
            provider="Databricks", tools=tools, data_scopes=scopes, criteria=criteria, identity=identity,
            versions=versions, extra=extra, raw=redacted)

    def _model_versions(self, host, creds, model):
        status, body = self._get(host, f"/api/2.1/unity-catalog/models/{quote(model, safe='.')}/versions", creds)
        if status != 200 or not isinstance(body, dict):
            return []
        vs = [v for v in body.get("model_versions") or [] if isinstance(v, dict)]
        return sorted(vs, key=lambda v: -int(v.get("version") or 0))

    def _function_summaries(self, host, creds, full_names):
        """Name, comment, first line and a body hash per function. The body itself is never kept."""
        out, cache = [], {}
        for full in full_names:
            parts = full.split(".")
            if len(parts) != 3:
                continue
            key = (parts[0], parts[1])
            if key not in cache:
                st, body = self._get(host, "/api/2.1/unity-catalog/functions", creds,
                                     {"catalog_name": parts[0], "schema_name": parts[1]})
                cache[key] = {f.get("full_name"): f for f in (body or {}).get("functions") or []} \
                    if st == 200 and isinstance(body, dict) else {}
            f = cache[key].get(full)
            if not f:
                continue
            text = f.get("routine_definition") or ""
            out.append({"full_name": full, "comment": f.get("comment"),
                        "first_line": text.splitlines()[0] if text else None,
                        "body_sha256": hashlib.sha256(text.encode()).hexdigest() if text else None})
        return out

    # -- apps
    def _apps(self, wid, host, creds):
        _, apps = self._paged(host, "/api/2.0/apps", "apps", creds, {"page_size": 100})
        out = []
        for a in apps:
            name = a.get("name")
            if not name:
                continue
            res = a.get("resources") or []
            tools, scopes, signal = [], [], "possible"
            for r in res:
                if "serving_endpoint" in r:
                    tools.append(r["serving_endpoint"].get("name"))
                    signal = "serving_endpoint_resource"
                elif "genie_space" in r:
                    tools.append(r["genie_space"].get("name") or r.get("name"))
                elif "job" in r:
                    tools.append(r["job"].get("id") or r.get("name"))
                elif "uc_securable" in r:
                    scopes.append(r["uc_securable"].get("securable_full_name"))
            st, dep = self._get(host, f"/api/2.0/apps/{quote(name, safe='')}/deployments", creds)
            deps = [d for d in (dep or {}).get("app_deployments") or [] if isinstance(d, dict)] \
                if st == 200 and isinstance(dep, dict) else []
            versions = [{"version": str(d.get("deployment_id") or UNKNOWN)[:8], "created": d.get("create_time")} for d in deps]
            active = (a.get("active_deployment") or {}).get("deployment_id")
            sp = a.get("service_principal_name")
            if sp:
                identity = {"type": "service_principal", "principal": sp,
                            "client_id": a.get("service_principal_client_id"), "distinct": True}
                if a.get("user_api_scopes"):
                    identity["obo_scopes"] = a["user_api_scopes"]
            else:
                identity = {"type": UNKNOWN, "principal": UNKNOWN, "distinct": False}
            tags = {t.get("key"): t.get("value") for t in a.get("tags") or [] if isinstance(t, dict)}
            out.append(SourceAgent(
                platform="databricks", native_id=f"{wid}:app:{a.get('id')}", name=name,
                description=a.get("description") or "", version=str(active)[:8] if active else UNKNOWN,
                owner=tags.get("owner") or a.get("creator"), status=(a.get("app_status") or {}).get("state") or UNKNOWN,
                last_updated=a.get("update_time"), url=a.get("url"), provider="Databricks",
                tools=[t for t in tools if t], data_scopes=[s for s in scopes if s], identity=identity, versions=versions,
                extra={"agent_signal": signal, "service_principal_client_id": a.get("service_principal_client_id"),
                       "workspace_id": wid, "workspace_host": host}, raw=a))
        return out

    # -- Agent Bricks
    _DATA_TOOLS = {"table", "volume", "catalog", "schema", "vector_search_index"}

    def _agent_bricks(self, wid, host, creds):
        """-> ([SourceAgent], {endpoint names owned by a KA or Supervisor})."""
        out, owned = [], set()
        _, kas = self._paged(host, "/api/2.1/knowledge-assistants", "knowledge_assistants", creds, {"page_size": 100})
        for k in kas:
            owned.add(k.get("endpoint_name"))
            out.append(SourceAgent(
                platform="databricks", native_id=f"{wid}:ka:{k.get('id')}", name=k.get("display_name") or k.get("id"),
                description=k.get("description") or "", version="1", owner=k.get("creator"),
                status=k.get("state") or UNKNOWN, last_updated=k.get("create_time"), provider="Databricks",
                identity={"type": "system_sp_passthrough", "principal": k.get("creator") or UNKNOWN, "distinct": False},
                versions=[{"version": "1", "created": k.get("create_time")}],
                extra={"agent_signal": "agent_bricks", "endpoint_name": k.get("endpoint_name"),
                       "experiment_id": k.get("experiment_id"), "workspace_id": wid, "workspace_host": host},
                raw={x: v for x, v in k.items() if x != "instructions"}))
        _, sas = self._paged(host, "/api/2.1/supervisor-agents", "supervisor_agents", creds, {"page_size": 100})
        for s in sas:
            sid = s.get("supervisor_agent_id") or str(s.get("name", "")).rsplit("/", 1)[-1]
            owned.add(s.get("endpoint_name"))
            tools, scopes = [], []
            st, tb = self._get(host, f"/api/2.1/supervisor-agents/{quote(str(sid), safe='')}/tools", creds)
            for t in (tb or {}).get("tools") or [] if st == 200 and isinstance(tb, dict) else []:
                kind = str(t.get("tool_type") or "").lower() or next(
                    (x for x in t if x not in ("name", "description", "tool_type")), "")
                inner = t.get(kind) if isinstance(t.get(kind), dict) else {}
                label = t.get("name") or inner.get("name") or inner.get("id")
                if kind in self._DATA_TOOLS:
                    if inner.get("name") or label:
                        scopes.append(inner.get("name") or label)
                elif label:
                    tools.append(label)
            out.append(SourceAgent(
                platform="databricks", native_id=f"{wid}:sa:{sid}", name=s.get("display_name") or sid,
                description=s.get("description") or "", version="1", owner=s.get("creator"), status=UNKNOWN,
                last_updated=s.get("create_time"), provider="Databricks", tools=tools, data_scopes=scopes,
                identity={"type": "system_sp_passthrough", "principal": s.get("creator") or UNKNOWN, "distinct": False},
                versions=[{"version": "1", "created": s.get("create_time")}],
                extra={"agent_signal": "agent_bricks", "endpoint_name": s.get("endpoint_name"),
                       "experiment_id": s.get("experiment_id"), "workspace_id": wid, "workspace_host": host},
                raw={x: v for x, v in s.items() if x != "instructions"}))
        owned.discard(None)
        return out, owned

    # -- UC agent services
    def _metastore_id(self, host, creds):
        st, body = self._get(host, "/api/2.1/unity-catalog/metastore_summary", creds)
        return (body or {}).get("metastore_id") if st == 200 and isinstance(body, dict) else None

    def _agent_services(self, wid, host, creds):
        _, items = self._paged(host, "/api/2.1/unity-catalog/agent-services", "agent_services", creds)
        if not items:
            return []
        ms = self._metastore_id(host, creds) or wid
        out = []
        for s in items:
            a = self._service_agent(s, ms)
            conn = ((s.get("config") or {}).get("source_connection") or {}).get("name")
            if conn:
                st, c = self._get(host, f"/api/2.1/unity-catalog/connections/{quote(conn.split('/')[-1], safe='.')}", creds)
                h = ((c or {}).get("options") or {}).get("host") if st == 200 and isinstance(c, dict) else None
                if h:
                    a.systems = [h]
            out.append(a)
        return out

    @staticmethod
    def _service_agent(s, metastore):
        full = str(s.get("name", "")).split("/", 1)[-1]
        cfg = s.get("config") or {}
        conn = str((cfg.get("source_connection") or {}).get("name") or "").split("/")[-1]
        return SourceAgent(
            platform="databricks", native_id=f"{metastore}:agent-service:{full}", name=_short(full),
            description=s.get("comment") or "", version="1", owner=s.get("created_by"), status="REGISTERED",
            last_updated=None, provider="External (via Databricks)",
            identity={"type": "external", "principal": conn or UNKNOWN, "distinct": True},
            versions=[{"version": "1", "created": None}],
            extra={"agent_service_type": s.get("agent_service_type"), "base_path": cfg.get("base_path"), "full_name": full},
            raw=s)

    # ------------------------------------------------------------ replay (demo) + sample mode
    def _replay(self):
        doc = load_fixture("databricks/agent-services.json")
        try:
            rows = load_fixture("databricks/agent-service-context.json").get("agent_context") or []
        except OSError:
            rows = []
        ctx = {r["full_name"]: r for r in rows if isinstance(r, dict) and r.get("full_name")}
        out = []
        for s in doc.get("agent_services", []):
            full = str(s.get("name", "")).split("/", 1)[-1]
            c = ctx.get(full) or {}
            props = c.get("properties") or {}
            ident = dict(c.get("identity") or {})
            if ident.get("type") == "none":
                ident["type"] = None  # no distinct identity; normalize treats a missing type as "none"
            a = self._service_agent(s, "replay")
            a.native_id = c.get("agent_service_id") or a.native_id
            a.status, a.provider = "READY", "Databricks"
            a.owner = s.get("created_by")
            a.last_updated = _iso_ms(c.get("updated_at"))
            a.tools, a.data_scopes = list(props.get("tools") or []), list(props.get("data_scopes") or [])
            a.systems, a.criteria = list(props.get("systems") or []), dict(props.get("criteria") or {})
            a.identity = ident
            a.versions = [{"version": "1", "created": _iso_ms(c.get("created_at"))}]
            a.extra.update({"endpoint": c.get("endpoint_name")})
            out.append(a)
        return out

    def _sample_agents(self):
        from backend.connections import sample
        extra = sample.tenant("databricks").get("agents") or []
        return self._replay() + [self._service_agent(s, "sample-metastore") for s in extra]

    def list_agents(self):
        if live_enabled("databricks"):
            host = os.environ["ACT_DATABRICKS_URL"]
            cfg = {"sample": False, "workspaces": [{"id": os.environ.get("ACT_DATABRICKS_WORKSPACE_ID", "0"), "host": host}]}
            return self.discover(cfg, {"token": os.environ.get("ACT_DATABRICKS_TOKEN", "")}, [])
        return self._replay()
