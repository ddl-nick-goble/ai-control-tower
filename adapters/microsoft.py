"""Microsoft adapter: one connection for Foundry (ARM + project data plane), Entra Agent ID (Graph) and the optional,
licence-gated Agent 365 catalog (Graph), merged on the Entra agent identity id. Research: azure-foundry.md.

Live calls go only through `_http`. Sample mode (cfg["sample"]) never touches the network.
Legacy replay (`list_agents`) reads fixtures/microsoft/ (real Graph catalog packages + an ACT-side overlay)."""
import base64
import copy
import json
import re
from datetime import datetime, timezone

from .base import AdapterBase, SourceAgent, fixtures_dir, live_enabled, load_fixture

ARM = "https://management.azure.com"
GRAPH = "https://graph.microsoft.com/v1.0"
ARM_V, ARG_V, PROJ_V, RA_V = "2022-12-01", "2024-04-01", "2025-06-01", "2022-04-01"
ARG_QUERY = ("resources | where type =~ 'microsoft.cognitiveservices/accounts' and kind =~ 'AIServices' "
             "| project id, name, kind, location, subscriptionId, resourceGroup, tenantId, properties")
SECRET_KEY = re.compile(r"(secret|token|passw|authorization|api[_-]?key|credential|headers|environment_variables|connection_string)", re.I)

AADSTS_FIX = {
    "AADSTS70021": "Azure hasn't finished saving the trust yet. Wait a few minutes and re-run the check; if it persists, re-run the setup command.",
    "AADSTS700211": "The trust points at a different Domino deployment. Re-run the setup command from this ACT.",
    "AADSTS700212": "The trust expects a different token audience. Re-run the setup command from this ACT.",
    "AADSTS700213": "The trust points at a different Domino identity. Re-run the setup command and check the crawl runs as the ACT service account.",
    "AADSTS700024": "The Domino token expired before use. Try again; if it repeats, check the Domino clock.",
    "AADSTS90002": "That tenant no longer exists. Reconnect.",
    "AADSTS700016": "That identity no longer exists. Re-run the setup command.",
    "AADSTS65001": "An admin must approve ACT once. Send them the consent link.",
    "AADSTS53003": "Your organization's sign-in policy blocked this. Use the keyless setup (re-run the setup command).",
}


def _http(method, url, **kw):
    """The only network call. -> (status, headers, json body)."""
    import requests
    r = requests.request(method, url, timeout=30, **kw)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, dict(r.headers), body


def _tok(creds, kind):
    c = creds or {}
    return c.get(f"{kind}_token") or c.get("access_token") or ""


def _call(creds, kind, method, url, **kw):
    headers = dict(kw.pop("headers", None) or {})
    headers["Authorization"] = "Bearer " + str(_tok(creds, kind))
    return _http(method, url, headers=headers, **kw)


def _errinfo(status, body):
    """-> (code, message) from an ARM/Graph/Foundry/AAD error body."""
    b = body or {}
    e = b.get("error")
    if isinstance(e, dict):
        return str(e.get("code") or status), str(e.get("message") or "")
    desc = str(b.get("error_description") or "")
    m = re.search(r"AADSTS\d+", desc)
    return (m.group(0) if m else str(e or status)), desc


def _arm_pages(creds, url, params=None):
    """ARM `nextLink` pages. -> (value items, error (status, body) or None)."""
    items, p = [], params
    while url:
        st, _, body = _call(creds, "arm", "GET", url, params=p)
        if st != 200:
            return items, (st, body)
        items += (body or {}).get("value") or []
        url, p = (body or {}).get("nextLink"), None
    return items, None


def _graph_pages(creds, url, params=None):
    items, p = [], params
    while url:
        st, _, body = _call(creds, "graph", "GET", url, params=p)
        if st != 200:
            return items, (st, body)
        items += (body or {}).get("value") or []
        url, p = (body or {}).get("@odata.nextLink"), None
    return items, None


def _foundry_pages(creds, url, params=None):
    """has_more + after=last_id pages. -> (data items, error or None)."""
    items, after = [], None
    while True:
        p = dict(params or {})
        if after:
            p["after"] = after
        st, _, body = _call(creds, "foundry", "GET", url, params=p)
        if st != 200:
            return items, (st, body)
        data = (body or {}).get("data") or []
        items += data
        after = (body or {}).get("last_id")
        if not ((body or {}).get("has_more") and after and data):
            return items, None


def _scrub(o):
    """Redact secret-bearing values (env vars, MCP headers, keys) before anything is stored in `raw`."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if SECRET_KEY.search(str(k)):
                out[k] = {kk: "[redacted]" for kk in v} if isinstance(v, dict) else "[redacted]"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(o, list):
        return [_scrub(x) for x in o]
    return o


def _iso(epoch):
    try:
        return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _host(url):
    m = re.match(r"https?://([^/:?#]+)", url or "")
    return m.group(1) if m else None


def _oid(token):
    try:
        seg = str(token).split(".")[1]
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))).get("oid")
    except Exception:
        return None


def _sample():
    return json.loads((fixtures_dir() / "sample_tenants" / "microsoft.json").read_text())


def _flag(nodes):
    for n in nodes:
        n["selected"] = True
        n.setdefault("kind", "subscription" if str(n.get("id", "")).startswith("az:sub") else "scope")
        n["children"] = _flag(n.get("children") or [])
    return nodes


def _role_can_change(rd):
    """True if a role definition allows anything but reads (so it can invoke or change agents)."""
    for perm in (rd or {}).get("permissions") or []:
        for a in list(perm.get("actions") or []) + list(perm.get("dataActions") or []):
            if not a.lower().endswith("/read"):
                return True
    return False


class MicrosoftAdapter(AdapterBase):
    platform = "microsoft"

    # ------------------------------------------------------------------ contract
    def auth_methods(self):
        return [
            {"id": "workload_identity", "label": "Connect without keys (workload identity federation)", "primary": True,
             "setup": "Run one Cloud Shell command that creates a managed identity trusted by ACT; nothing secret is stored"},
            {"id": "oauth", "label": "Sign in with Microsoft", "primary": False,
             "setup": "Pop-up sign-in with your own account; an admin approves ACT once"},
        ]

    def _accounts(self, creds):
        """Foundry accounts through Resource Graph ($skipToken pages). -> (accounts, error)."""
        out, skip = [], None
        while True:
            opts = {"$top": 1000, "resultFormat": "objectArray"}
            if skip:
                opts["$skipToken"] = skip
            st, _, body = _call(creds, "arm", "POST", f"{ARM}/providers/Microsoft.ResourceGraph/resources",
                                params={"api-version": ARG_V}, json={"query": ARG_QUERY, "options": opts})
            if st != 200:
                return out, (st, body)
            out += (body or {}).get("data") or []
            skip = (body or {}).get("$skipToken")
            if not skip:
                return out, None

    def _projects(self, creds, acct):
        return _arm_pages(creds, f"{ARM}{acct['id']}/projects", {"api-version": PROJ_V})

    @staticmethod
    def _endpoint(acct, project_name):
        sub = (acct.get("properties") or {}).get("customSubDomainName") or acct.get("name")
        return f"https://{sub}.services.ai.azure.com/api/projects/{project_name.split('/')[-1]}"

    def _item(self, name, detail, required, ok, fix):
        return {"name": name, "detail": detail, "required": required, "ok": ok, "fix": None if ok else fix}

    def test_connection(self, cfg, creds):
        if cfg.get("sample"):
            return [{"name": p["name"], "detail": p.get("detail", ""), "required": bool(p.get("required")),
                     "ok": not p.get("missing_first"), "fix": p["fix"] if p.get("missing_first") else None}
                    for p in _sample()["permissions"]]
        items = []
        # 1. Azure resources (also proves the sign-in)
        st, _, body = _call(creds, "arm", "GET", f"{ARM}/subscriptions", params={"api-version": ARM_V})
        code, msg = _errinfo(st, body)
        aad = re.search(r"AADSTS\d+", code + " " + msg)
        subs = (body or {}).get("value") or [] if st == 200 else []
        if aad:
            fix = AADSTS_FIX.get(aad.group(0), "Microsoft refused the sign-in. Re-run the setup command from this ACT, and check "
                                                "the crawl runs as the ACT service account.")
            items.append(self._item("Microsoft accepted ACT's sign-in", "login.microsoftonline.com token exchange", True, False, fix))
        else:
            items.append(self._item("Microsoft accepted ACT's sign-in", "login.microsoftonline.com token exchange", True,
                                    st != 401, "Re-run the setup command so Microsoft trusts ACT's Domino service account."))
        items.append(self._item(
            "Azure resources (subscriptions, Resource Graph)", "Microsoft.Resources/subscriptions/read", True, st == 200,
            "ACT can't read subscriptions. Assign the Reader role to ACT's managed identity on the subscription or management "
            "group (the setup command does this), then re-run the check."))
        # 2. Foundry
        fix_f = ("ACT can see the Foundry resource but not its agents. Assign ACT Foundry Inventory Reader on it: az role assignment "
                 "create --assignee <act-reader-principal-id> --role \"ACT Foundry Inventory Reader\" --scope <foundry-resource-id>")
        accts, aerr = self._accounts(creds) if st == 200 else ([], (st, body))
        public = [a for a in accts if (a.get("properties") or {}).get("publicNetworkAccess") != "Disabled"]
        fstate = None
        for a in public[:1]:
            projs, _ = self._projects(creds, a)
            if projs:
                fst, _, fb = _call(creds, "foundry", "GET", self._endpoint(a, projs[0]["name"]) + "/agents",
                                   params={"api-version": "v1", "limit": 1})
                fstate = (fst, fb)
        if fstate is None:
            items.append(self._item("Microsoft Foundry agents", "Microsoft.CognitiveServices/accounts/AIServices/agents/read",
                                    False, False, "ACT couldn't reach a Foundry project yet (it needs Reader on the subscription "
                                    "first). Then also assign ACT Foundry Inventory Reader on the Foundry resource: " + fix_f.split(": ", 1)[1]))
        else:
            items.append(self._item("Microsoft Foundry agents", "Microsoft.CognitiveServices/accounts/AIServices/agents/read",
                                    False, fstate[0] == 200, fix_f))
        # 3. Entra agent identities
        gst, _, gb = _call(creds, "graph", "GET", f"{GRAPH}/servicePrincipals/microsoft.graph.agentIdentity", params={"$top": 1})
        items.append(self._item("Entra agent identities (Agent ID)", "Graph: AgentIdentity.Read.All", False, gst == 200,
                                "Directory read wasn't granted. A Privileged Role Administrator or Global Administrator must run the "
                                "Graph part of the setup command (AgentIdentity.Read.All), then re-run the check."))
        # 4. Agent 365 catalog
        pst, _, pb = _call(creds, "graph", "GET", f"{GRAPH}/copilot/admin/catalog/packages", params={"$top": 1})
        items.append(self._item("Agent 365 catalog", "Graph: CopilotPackages.Read.All (needs an Agent 365 licence)", False, pst == 200,
                                "The Agent 365 catalog needs an Agent 365 licence. Foundry and Entra agents still work without it."))
        # 5. read-only
        items.append(self._readonly_item(cfg, creds, subs))
        return items

    def _readonly_item(self, cfg, creds, subs):
        name = "Access stays read-only"
        principal = cfg.get("principal_id") or _oid(_tok(creds, "arm")) or cfg.get("client_id")
        flagged, failed = [], False
        for s in subs[:5]:
            sid = s.get("subscriptionId")
            ras, err = _arm_pages(creds, f"{ARM}/subscriptions/{sid}/providers/Microsoft.Authorization/roleAssignments",
                                  {"api-version": RA_V, "$filter": f"assignedTo('{principal}')"})
            if err:
                failed = True
                continue
            for ra in ras:
                pr = ra.get("properties") or {}
                st, _, rd = _call(creds, "arm", "GET", ARM + str(pr.get("roleDefinitionId")), params={"api-version": RA_V})
                props = (rd or {}).get("properties") or {}
                if st == 200 and _role_can_change(props):
                    flagged.append(f"{props.get('roleName', 'a role')} at {pr.get('scope')}")
        if flagged:
            return {"name": name, "required": False, "ok": False, "detail": "Granted: " + "; ".join(flagged),
                    "fix": "ACT's identity holds " + "; ".join(flagged) + ", which can also invoke or change agents. ACT only needs "
                           "read-only access (Reader plus ACT Foundry Inventory Reader). Remove that role, then re-run the check."}
        if failed:
            return {"name": name, "required": False, "ok": False, "detail": "Role assignments could not be read",
                    "fix": "ACT couldn't read role assignments to confirm it is read-only. Assign Reader, then re-run the check."}
        return {"name": name, "required": False, "ok": True, "fix": None,
                "detail": "No role that can invoke or change agents was found"}

    def list_scopes(self, cfg, creds):
        if cfg.get("sample"):
            return _flag(copy.deepcopy(_sample()["tree"]))
        subs, err = _arm_pages(creds, f"{ARM}/subscriptions", {"api-version": ARM_V})
        if err and not subs:
            raise RuntimeError("Azure refused the subscription list. Assign Reader to ACT's identity and re-run setup.")
        accts, _ = self._accounts(creds)
        by_sub = {}
        for a in accts:
            by_sub.setdefault(a.get("subscriptionId"), []).append(a)
        sub_nodes = []
        for s in subs:
            sid = s["subscriptionId"]
            acct_nodes = []
            for a in by_sub.get(sid, []):
                projs, _ = self._projects(creds, a)
                private = (a.get("properties") or {}).get("publicNetworkAccess") == "Disabled"
                acct_nodes.append({
                    "id": f"az:acct:{a['id']}", "kind": "foundry_resource", "selected": True,
                    "label": a["name"] + (" (private network)" if private else ""),
                    "children": [{"id": f"az:proj:{p['id']}", "kind": "project", "selected": True, "children": [],
                                  "label": f"project {p['name'].split('/')[-1]}"} for p in projs]})
            sub_nodes.append({"id": f"az:sub:{sid}", "kind": "subscription", "selected": True,
                              "label": s.get("displayName") or sid, "children": acct_nodes})
        tid = cfg.get("tenant_id") or "tenant"
        return [{"id": f"az:tenant:{tid}", "kind": "tenant", "selected": True, "label": f"Tenant {tid} (Entra agents and Agent 365)",
                 "children": sub_nodes}]

    def coverage(self, cfg, creds, scopes):
        out = [{"reason": "Classic Foundry agents (Assistants API) retire on 2027-03-31 and hub-based Foundry projects are not scanned.",
                "fix": "Move classic agents to the current Foundry agent model, or add them to ACT by hand."}]
        if cfg.get("sample"):
            out.append({"reason": "Agent 365 catalog isn't available without an Agent 365 licence.",
                        "fix": "Get an Agent 365 licence for the tenant; Foundry and Entra agents are found without it."})
            return out
        accts, _ = self._accounts(creds)
        for a in accts:
            if (a.get("properties") or {}).get("publicNetworkAccess") == "Disabled":
                out.append({"reason": f"Foundry resource {a['name']} is private (public network access is off), so ACT lists it "
                                      "from Azure metadata only and can't read its agents.",
                            "fix": "Peer Domino's network to it or add a private endpoint reachable from Domino, then re-run discovery."})
        pst, _, _ = _call(creds, "graph", "GET", f"{GRAPH}/copilot/admin/catalog/packages", params={"$top": 1})
        if pst != 200:
            out.append({"reason": "Agent 365 catalog isn't available, so agents published only there are not listed.",
                        "fix": "The catalog needs an Agent 365 licence and the CopilotPackages.Read.All permission. Foundry and Entra "
                               "agents are unaffected."})
        gst, _, _ = _call(creds, "graph", "GET", f"{GRAPH}/servicePrincipals/microsoft.graph.agentIdentity", params={"$top": 1})
        if gst != 200:
            out.append({"reason": "Entra agent identities can't be read, so owners and Entra-only agents are missing.",
                        "fix": "A Privileged Role Administrator must grant AgentIdentity.Read.All to ACT's managed identity."})
        return out

    # ------------------------------------------------------------------ discovery
    def discover(self, cfg, creds, scopes, progress=None):
        if cfg.get("sample"):
            return self._sample_discover()
        scopes = set(scopes or [])
        subs = [s.split(":", 2)[2] for s in scopes if s.startswith("az:sub:")]
        proj_ids = sorted(s[len("az:proj:"):] for s in scopes if s.startswith("az:proj:"))
        tenant_wide = any(s.startswith("az:tenant:") for s in scopes)
        recs = {}  # merge key -> record

        def rec(key):
            return recs.setdefault(key, {"source": [], "foundry": None, "identity": None, "package": None, "project": None,
                                         "meta": {}, "versions": [], "deployments": [], "connections": []})
        accts, _ = self._accounts(creds)
        accts = {a["id"]: a for a in accts}
        proj_meta = {}
        for aid, a in accts.items():
            if any(p.startswith(aid + "/projects/") for p in proj_ids):
                for p in self._projects(creds, a)[0]:
                    proj_meta[p["id"]] = (a, p)
        total = len(proj_ids) + 2
        for i, pid in enumerate(proj_ids):
            if progress:
                progress(i, total, f"Reading Foundry project {pid.rsplit('/', 1)[-1]}")
            a, p = proj_meta.get(pid, (accts.get(pid.split("/projects/")[0]), None))
            if not a or (a.get("properties") or {}).get("publicNetworkAccess") == "Disabled":
                continue  # private network: reported by coverage(), listed from ARM metadata only
            self._foundry(creds, a, p or {"name": pid.rsplit("/", 1)[-1], "id": pid}, rec)
        if tenant_wide or not scopes:
            if progress:
                progress(len(proj_ids), total, "Reading Entra agent identities")
            self._entra(creds, rec)
            if progress:
                progress(len(proj_ids) + 1, total, "Reading the Agent 365 catalog")
            self._agent365(creds, rec)
        roles = {}
        out = [self._to_agent(creds, subs, key, r, roles) for key, r in recs.items()]
        if progress:
            progress(total, total, f"Found {len(out)} agents")
        return out

    def _foundry(self, creds, acct, proj, rec):
        base = self._endpoint(acct, proj["name"])
        agents, err = _foundry_pages(creds, base + "/agents", {"api-version": "v1", "limit": 100})
        if err and not agents:
            return
        deps, _ = self._foundry_value(creds, base + "/deployments")
        conns, _ = self._foundry_value(creds, base + "/connections")
        for ag in agents:
            ii = ag.get("instance_identity") or {}
            key = ii.get("principal_id") or f"{proj['id']}/agents/{ag.get('name')}"
            r = rec(key)
            r.update(foundry=ag, project=proj, base=base, deployments=deps, connections=conns, account=acct)
            r["source"].append("foundry")
            vers, verr = _foundry_pages(creds, f"{base}/agents/{ag['name']}/versions", {"api-version": "v1", "limit": 100})
            r["versions"] = vers

    @staticmethod
    def _foundry_value(creds, url):
        st, _, body = _call(creds, "foundry", "GET", url, params={"api-version": "v1"})
        return ((body or {}).get("value") or [], None) if st == 200 else ([], (st, body))

    def _entra(self, creds, rec):
        ids, err = _graph_pages(creds, f"{GRAPH}/servicePrincipals/microsoft.graph.agentIdentity", {"$top": 100})
        for sp in ids:
            r = rec(sp["id"])
            r["identity"] = sp
            if "entra_agent_id" not in r["source"]:
                r["source"].append("entra_agent_id")

    def _agent365(self, creds, rec):
        pkgs, err = _graph_pages(creds, f"{GRAPH}/copilot/admin/catalog/packages", {"$top": 100})
        if err and not pkgs:
            return
        for pk in pkgs:
            r = rec(pk.get("agentIdentityId") or f"m365:{pk['id']}")
            r["package"] = pk
            if "agent365" not in r["source"]:
                r["source"].append("agent365")

    def _owner(self, creds, pid):
        for kind in ("owners", "sponsors"):
            vals, err = _graph_pages(creds, f"{GRAPH}/servicePrincipals/{pid}/{kind}")
            if vals:
                u = vals[0]
                return u.get("userPrincipalName") or u.get("displayName") or u.get("mail")
        return None

    def _role_scopes(self, creds, subs, pid, roles):
        out = []
        for sid in subs:
            ras, err = _arm_pages(creds, f"{ARM}/subscriptions/{sid}/providers/Microsoft.Authorization/roleAssignments",
                                  {"api-version": RA_V, "$filter": f"assignedTo('{pid}')"})
            for ra in ras:
                pr = ra.get("properties") or {}
                rid = str(pr.get("roleDefinitionId"))
                if rid not in roles:
                    st, _, rd = _call(creds, "arm", "GET", ARM + rid, params={"api-version": RA_V})
                    roles[rid] = ((rd or {}).get("properties") or {}).get("roleName") or rid.rsplit("/", 1)[-1] if st == 200 else rid.rsplit("/", 1)[-1]
                out.append(f"{roles[rid]} on {pr.get('scope')}")
        return out

    def _to_agent(self, creds, subs, key, r, roles):
        fa, sp, pk = r["foundry"], r["identity"], r["package"]
        pid = (((fa or {}).get("instance_identity") or {}).get("principal_id")) or (sp or {}).get("id") or (pk or {}).get("agentIdentityId")
        latest = (((fa or {}).get("versions") or {}).get("latest")) or {}
        definition = latest.get("definition") or {}
        deps = {d.get("name"): d for d in r["deployments"]}
        conns = {c.get("name"): c for c in r["connections"]}
        tools, systems, data_scopes, mcp, agents_dep, criteria = [], [], [], [], [], {}
        for t in definition.get("tools") or []:
            ty = t.get("type")
            if ty == "mcp":
                tools.append(f"{t.get('server_label', 'mcp')}:{','.join(t['allowed_tools'])}" if t.get("allowed_tools") else t.get("server_label", "mcp"))
                if t.get("server_url"):
                    mcp.append(t["server_url"])
                    systems.append(_host(t["server_url"]))
                if t.get("require_approval"):
                    criteria["require_approval"] = t["require_approval"]
            elif ty == "function" or ty == "openapi":
                tools.append(t.get("name") or (t.get("openapi") or {}).get("name") or ty)
            elif ty in ("a2a", "a2a_preview"):
                tools.append(ty)
                if t.get("base_url"):
                    agents_dep.append(t["base_url"])
                    systems.append(_host(t["base_url"]))
            else:
                tools.append(ty)
            if ty == "azure_ai_search":
                for ix in (t.get("azure_ai_search") or {}).get("indexes") or []:
                    c = conns.get(ix.get("project_connection_id")) or {}
                    tgt = str(c.get("target") or "").split("?")[0]
                    data_scopes.append(f"{ix.get('index_name')}@{tgt}" if tgt else str(ix.get("index_name")))
                    if tgt:
                        systems.append(_host(tgt))
            for vs in t.get("vector_store_ids") or []:
                data_scopes.append(vs)
            if ty in ("code_interpreter", "computer_use_preview", "browser_automation"):
                criteria.setdefault("risky_tools", []).append(ty)
        if pid:
            data_scopes += self._role_scopes(creds, subs, pid, roles)
        model = deps.get(definition.get("model")) if definition.get("model") else None
        models = [f"{model.get('modelName')}@{model.get('modelVersion')}"] if model else []
        provider = (model or {}).get("modelPublisher") or ((pk or {}).get("publisher") if pk and not fa else None) or (
            "unknown" if not fa and not pk else "Microsoft")
        # identity
        ii = (fa or {}).get("instance_identity")
        if ii and ii.get("principal_id"):
            ident = {"type": "entra_agent_id", "principal": ii["principal_id"], "client_id": ii.get("client_id"), "distinct": True}
        elif sp:
            ident = {"type": "entra_agent_id", "principal": sp["id"], "distinct": True}
        elif pk and pk.get("agentIdentityId"):
            ident = {"type": "entra_agent_id", "principal": pk["agentIdentityId"], "distinct": True}
        elif fa:
            ident = {"type": "project_managed_identity", "principal": "unknown", "distinct": False}
        else:
            ident = {"type": "none", "principal": "unknown", "distinct": False}
        if sp:
            ident["blueprint"] = sp.get("agentIdentityBlueprintId")
        # owner: Graph owners, sponsors, metadata.owner, project tags.Owner, project creator
        owner = self._owner(creds, pid) if (pid and (fa or sp)) else None
        proj = r["project"] or {}
        tags = {str(k).lower(): v for k, v in (proj.get("tags") or {}).items()}
        owner = owner or latest.get("metadata", {}).get("owner") or tags.get("owner") or (proj.get("systemData") or {}).get("createdBy")
        # status
        if fa:
            status = f"{fa.get('state', 'unknown')}/{latest.get('status', 'unknown')}"
        elif sp:
            status = "ENABLED" if sp.get("accountEnabled", True) else "DISABLED"
        else:
            status = "unknown"
        if sp and sp.get("accountEnabled") is False:
            status = "DISABLED" if not fa else status + " (Entra account disabled)"
        if pk:
            if pk.get("isBlocked"):
                status = "BLOCKED" if not fa and not sp else status + " (blocked in Agent 365)"
            elif not fa and not sp:
                status = "AVAILABLE"
        # versions
        vers = sorted(r["versions"], key=lambda v: v.get("created_at") or 0, reverse=True) or ([latest] if latest else [])
        versions = [{"version": str(v.get("version")), "created": _iso(v.get("created_at")), "status": v.get("status")} for v in vers]
        version = str(latest.get("version") or (pk or {}).get("version") or "1")
        if not versions:
            versions = [{"version": version, "created": (pk or {}).get("createdDateTime") or (sp or {}).get("createdDateTime")}]
        name = (fa or {}).get("name") or (sp or {}).get("displayName") or (pk or {}).get("displayName") or key
        updated = [x for x in (_iso(latest.get("created_at")), (pk or {}).get("lastModifiedDateTime"), (sp or {}).get("createdDateTime")) if x]
        native = (f"{proj['id']}/agents/{fa['name']}" if fa and proj.get("id") else
                  f"entra:{sp['id']}" if sp else f"m365:{pk['id']}" if pk else key)
        extra = {"source": r["source"], "package_id": (pk or {}).get("id"), "kind": definition.get("kind"),
                 "project": proj.get("name"), "governance": (pk or {}).get("governanceMetadata"),
                 "hosts": (pk or {}).get("supportedHosts"), "created_by_app": (sp or {}).get("createdByAppId"),
                 "agent_card_skills": [s.get("name") for s in ((fa or {}).get("agent_card") or {}).get("skills") or []]}
        return SourceAgent(
            platform="microsoft", native_id=native, name=name,
            description=latest.get("description") or (pk or {}).get("shortDescription") or "", version=version,
            owner=owner or "unknown", status=status, last_updated=max(updated) if updated else None,
            url=(fa or {}).get("agent_endpoint") if isinstance((fa or {}).get("agent_endpoint"), str) else (
                f"{r.get('base')}/agents/{fa['name']}" if fa else None),
            provider=provider, tools=tools, data_scopes=data_scopes, systems=[s for s in dict.fromkeys(systems) if s],
            value_limits={}, criteria=criteria, identity=ident, versions=versions,
            dependencies={"models": models, "mcp_servers": mcp, "agents": agents_dep}, extra=extra,
            raw=_scrub({"agent": fa, "identity": sp, "package": pk}))

    # ------------------------------------------------------------------ sample and legacy replay
    def _sample_discover(self):
        extra = [SourceAgent(platform="microsoft", **a) for a in _sample().get("agents", [])]
        return self.list_agents() + extra

    def list_agents(self):
        """Replay: fixtures/microsoft/ holds Graph catalog packages (P_ ids) and an ACT overlay for what the catalog lacks."""
        if live_enabled("microsoft"):
            import os
            cfg = json.loads(os.environ.get("ACT_MICROSOFT_CFG", "{}"))
            creds = {"access_token": os.environ.get("ACT_MICROSOFT_TOKEN", "")}
            ids = [n["id"] for n in self._flat(self.list_scopes(cfg, creds))]
            return self.discover(cfg, creds, ids)
        overlay = load_fixture("microsoft/act-overlay.json").get("overlay", {})
        out = []
        for p in load_fixture("microsoft/list-packages.json").get("value", []):
            ov = overlay.get(p["id"], {})
            out.append(SourceAgent(
                platform="microsoft", native_id=ov.get("native_id") or p.get("agentIdentityId") or p["id"],
                name=p.get("displayName") or p["id"], description=p.get("shortDescription") or "",
                version=str(p.get("version") or "1"), owner=ov.get("owner") or "unknown",
                status="BLOCKED" if p.get("isBlocked") else "AVAILABLE", last_updated=p.get("lastModifiedDateTime"),
                provider=p.get("publisher") or "Microsoft", tools=list(ov.get("tools") or []),
                data_scopes=list(ov.get("data_scopes") or []), systems=list(ov.get("systems") or []), value_limits={},
                criteria=dict(ov.get("criteria") or {}), identity=dict(ov.get("identity") or {}),
                versions=[{"version": str(p.get("version") or "1"), "created": p.get("createdDateTime")}],
                extra={"source": ["agent365"], "package_id": p["id"], "display_name": ov.get("display_name"),
                       "hosts": p.get("supportedHosts")}, raw=p))
        return out

    @staticmethod
    def _flat(tree):
        for n in tree:
            yield n
            yield from MicrosoftAdapter._flat(n.get("children") or [])
