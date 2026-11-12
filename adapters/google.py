"""Google adapter: one connection for Agent Runtime (aiplatform reasoningEngines), Agent Registry and Gemini
Enterprise (Discovery Engine), merged on the reasoningEngine name. Research: build/research/providers/google-*.md.

Live calls go only through `_http`. Sample mode (cfg["sample"]) never touches the network.
Legacy replay (`list_agents`) reads fixtures/google/ (real Agent Registry v1 shapes + an ACT-side overlay)."""
import copy
import json
import re

from .base import AdapterBase, SourceAgent, fixtures_dir, live_enabled, load_fixture

AI = "https://{loc}-aiplatform.googleapis.com"
REGISTRY = "https://agentregistry.googleapis.com/v1"
GE = "https://discoveryengine.googleapis.com/v1alpha"
CRM = "https://cloudresourcemanager.googleapis.com/v3"
CAI = "https://cloudasset.googleapis.com/v1"
GE_LOCS = ("global", "us", "eu")
RT_REF = "agentregistry.googleapis.com/system/RuntimeReference"
RT_IDENT = "agentregistry.googleapis.com/system/RuntimeIdentity"
RT_FRAMEWORK = "agentregistry.googleapis.com/system/Framework"
SECRET_RE = re.compile(r"(secret|token|passw|key|credential|auth)", re.I)

# (item name, detail, required, permissions, role to add, fix target)
FAMILIES = [
    ("Read Agent Runtime", "aiplatform.reasoningEngines.list", True,
     ["aiplatform.reasoningEngines.list", "aiplatform.reasoningEngines.get", "aiplatform.reasoningEngines.getIamPolicy"],
     "the ACT agent inventory reader role (it reads Agent Runtime without being able to query agents)"),
    ("Read the Agent Registry", "agentregistry.agents.list", False,
     ["agentregistry.agents.list", "agentregistry.agents.get", "agentregistry.bindings.list"], "roles/agentregistry.viewer"),
    ("Read Gemini Enterprise", "discoveryengine.agents.list", False,
     ["discoveryengine.agents.list", "discoveryengine.engines.list"], "roles/discoveryengine.viewer"),
    ("Find agents org-wide (Cloud Asset)", "cloudasset.assets.searchAllResources", False,
     ["cloudasset.assets.searchAllResources", "cloudasset.assets.searchAllIamPolicies"], "roles/cloudasset.viewer"),
    ("Read audit logs", "logging.logEntries.list", False, ["logging.logEntries.list"], "roles/logging.viewer"),
    ("Read traces", "cloudtrace.traces.list", False, ["cloudtrace.traces.list"], "roles/cloudtrace.user"),
]
# Permissions that let a role invoke or change agents; ACT only reads.
RISKY = ["aiplatform.reasoningEngines.query", "aiplatform.reasoningEngines.streamQuery", "aiplatform.reasoningEngines.create",
         "aiplatform.reasoningEngines.update", "aiplatform.reasoningEngines.delete", "discoveryengine.assistants.assist",
         "discoveryengine.agents.create", "discoveryengine.agents.update", "discoveryengine.agents.delete"]


def _http(method, url, **kw):
    """The only network call. -> (status, headers, json body)."""
    import requests
    r = requests.request(method, url, timeout=30, **kw)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, dict(r.headers), body


def _call(creds, method, url, **kw):
    headers = dict(kw.pop("headers", None) or {})
    headers["Authorization"] = "Bearer " + str((creds or {}).get("access_token") or "")
    return _http(method, url, headers=headers, **kw)


def _err(status, body):
    """-> (reason, metadata, message) from a Google error body (key on details[].reason)."""
    e = (body or {}).get("error") or {}
    if not isinstance(e, dict):
        return str(e), {}, (body or {}).get("error_description", "")
    for d in e.get("details") or []:
        if isinstance(d, dict) and d.get("reason"):
            return d["reason"], d.get("metadata") or {}, e.get("message", "")
    return e.get("status") or str(status), {}, e.get("message", "")


def _paged(creds, url, key, params=None):
    """Follow nextPageToken. -> (items, error) where error is (status, body) of the first failing page or None."""
    items, token = [], None
    while True:
        p = dict(params or {})
        if token:
            p["pageToken"] = token
        st, _, body = _call(creds, "GET", url, params=p)
        if st != 200:
            return items, (st, body)
        items += (body or {}).get(key) or []
        token = (body or {}).get("nextPageToken")
        if not token:
            return items, None


def _sample():
    return json.loads((fixtures_dir() / "sample_tenants" / "google.json").read_text())


def _flag(nodes):
    for n in nodes:
        n["selected"] = True
        n.setdefault("kind", "project" if str(n.get("id", "")).startswith("gcp:proj") else "organization")
        n["children"] = _flag(n.get("children") or [])
    return nodes


def _short(name):
    return (name or "").rsplit("/", 1)[-1]


def _identity_from(principal, itype=None):
    if not principal:
        return {"type": "google_managed", "principal": "unknown", "distinct": False}
    if principal.startswith("principal://"):
        t = "agent_identity"
    elif principal.startswith("service-") and "gcp-sa-aiplatform-re" in principal:
        t = "service_agent_default"
    else:
        t = "service_account"
    return {"type": itype or t, "principal": principal, "distinct": not principal.startswith("service-")}


def _scrub_engine(engine):
    """Copy of a reasoningEngine with env values and secret references removed."""
    e = copy.deepcopy(engine)
    dep = (e.get("spec") or {}).get("deploymentSpec") or {}
    for v in dep.get("env") or []:
        if isinstance(v, dict) and "value" in v:
            v["value"] = "[redacted]"
    for v in dep.get("secretEnv") or []:
        if isinstance(v, dict):
            v["secret"] = "[redacted]"
    return e


class GoogleAdapter(AdapterBase):
    platform = "google"

    # ------------------------------------------------------------------ contract
    def auth_methods(self):
        return [
            {"id": "workload_identity", "label": "Connect without keys (workload identity federation)", "primary": True,
             "setup": "Run one Cloud Shell command; nothing secret is stored"},
            {"id": "oauth", "label": "Sign in with Google", "primary": False,
             "setup": "Pop-up sign-in with your own account; needs an OAuth client"},
            {"id": "service_account_key", "label": "Upload a service account key", "primary": False,
             "setup": "Last resort; many organizations block key creation"},
        ]

    @staticmethod
    def _host(cfg):
        return cfg.get("host") or cfg.get("project") or "your project"

    def test_connection(self, cfg, creds):
        if cfg.get("sample"):
            return [{"name": p["name"], "detail": p.get("detail", ""), "required": bool(p.get("required")),
                     "ok": not p.get("missing_first"), "fix": p["fix"] if p.get("missing_first") else None}
                    for p in _sample()["permissions"]]
        host, loc = self._host(cfg), cfg.get("location") or "us-central1"
        items = []
        signed = bool((creds or {}).get("access_token"))
        items.append({"name": "Google accepted ACT's sign-in", "detail": "Federated access token", "required": True,
                      "ok": signed, "fix": None if signed else "Re-run the setup command so Google trusts ACT's Domino service account."})
        asked = sorted({p for f in FAMILIES for p in f[3]} | set(RISKY))
        st, _, body = _call(creds, "POST", f"{CRM}/projects/{host}:testIamPermissions", json={"permissions": asked})
        granted = set((body or {}).get("permissions") or []) if st == 200 else set()
        probes = {
            "Read Agent Runtime": f"{AI.format(loc=loc)}/v1/projects/{host}/locations/{loc}/reasoningEngines",
            "Read the Agent Registry": f"{REGISTRY}/projects/{host}/locations/{loc}/agents",
            "Read Gemini Enterprise": f"{GE}/projects/{host}/locations/global/collections",
        }
        for name, detail, required, perms, role in FAMILIES:
            missing = [p for p in perms if p not in granted]
            problem = None
            if name in probes:
                pst, _, pbody = _call(creds, "GET", probes[name], params={"pageSize": 1})
                if pst != 200:
                    reason, meta, _msg = _err(pst, pbody)
                    if reason == "SERVICE_DISABLED":
                        problem = (f"{name.replace('Read ', '')} isn't turned on in {host}, so it has no agents to read. "
                                   f"Turn on {meta.get('service', 'the API')} in {host}"
                                   + (f" ({meta['activationUrl']})" if meta.get("activationUrl") else "")
                                   + ", then re-run the check.")
                    elif reason == "IAM_PERMISSION_DENIED":
                        perm = meta.get("permission") or detail
                        missing = sorted(set(missing) | {perm})
            if problem:
                items.append({"name": name, "detail": detail + " (API is off)", "required": required, "ok": False, "fix": problem})
            elif missing:
                items.append({"name": name, "detail": detail + ": missing " + ", ".join(missing), "required": required,
                              "ok": False,
                              "fix": f"ACT can't read {name.replace('Read ', '')} in {host} (missing {', '.join(missing)}). "
                                     f"Add {role} to ACT's identity, or re-run setup, which adds it."})
            else:
                items.append({"name": name, "detail": detail, "required": required, "ok": True, "fix": None})
        risky = sorted(set(RISKY) & granted)
        if risky:
            items.append({"name": "Access stays read-only", "required": False, "ok": False,
                          "detail": "Granted: " + ", ".join(risky),
                          "fix": "ACT's role can also query (invoke) or change agents (" + ", ".join(risky) + "). ACT only needs "
                                 "read-only access: use the ACT agent inventory reader role instead of roles/aiplatform.viewer, "
                                 "or remove the extra permissions, then re-run the check."})
        else:
            items.append({"name": "Access stays read-only", "required": False, "ok": True,
                          "detail": "No permission to invoke or change agents was granted", "fix": None})
        return items

    def list_scopes(self, cfg, creds):
        if cfg.get("sample"):
            return _flag(copy.deepcopy(_sample()["tree"]))
        org = cfg.get("org")
        projects, err = _paged(creds, f"{CRM}/projects:search", "projects", {"query": "state:ACTIVE", "pageSize": 100})
        if err and not projects:
            reason, meta, msg = _err(*err)
            raise RuntimeError(f"Google refused the project list ({reason}). Re-run setup so ACT can read projects.")
        locs = {}  # project id -> set(location)
        if org:
            res, cerr = _paged(creds, f"{CAI}/organizations/{org}:searchAllResources", "results",
                               {"assetTypes": "aiplatform.googleapis.com/ReasoningEngine", "pageSize": 500})
            for r in res:
                m = re.search(r"/projects/([^/]+)/locations/([^/]+)/", r.get("name", ""))
                if m:
                    locs.setdefault(m.group(1), set()).add(m.group(2))
        children = []
        for p in projects:
            pid = p.get("projectId")
            names = sorted(locs.get(pid, set()) | {"global"} | ({cfg.get("location") or "us-central1"} if not locs.get(pid) else set()))
            children.append({"id": f"gcp:proj:{pid}", "label": p.get("displayName") or pid, "kind": "project", "selected": True,
                             "number": p.get("projectNumber"),
                             "children": [{"id": f"gcp:loc:{pid}:{n}", "label": n, "kind": "location", "selected": True,
                                           "children": []} for n in names]})
        return [{"id": f"gcp:org:{org or 'projects'}", "label": f"Organization {org}" if org else "Projects", "kind": "organization",
                 "selected": True, "children": children}]

    def coverage(self, cfg, creds, scopes):
        out = [{"reason": "Gemini Enterprise only lists agents visible to the caller (the agents created by or shared with ACT's identity), so agents built by "
                          "employees in Workflow Builder under other accounts may be missing from this inventory.",
                "fix": "Ask owners to register those agents in the Agent Registry, or grant ACT's identity access to the "
                       "Gemini Enterprise app so they become visible."},
               {"reason": "Agents on Cloud Run or GKE appear only if they opted in to the Agent Registry.",
                "fix": "Label the service for Agent Registry registration, or add it by hand there."}]
        if cfg.get("sample"):
            return out
        for it in self.test_connection(cfg, creds):
            if not it["ok"] and it["name"] != "Access stays read-only":
                out.append({"reason": f"{it['name']} is unavailable, so agents on that surface won't be found. {it['detail']}",
                            "fix": it["fix"]})
        return out

    # ------------------------------------------------------------------ discovery
    def discover(self, cfg, creds, scopes, progress=None):
        if cfg.get("sample"):
            return self._sample_discover()
        scopes = set(scopes or [])
        targets = []  # (project, location)
        for s in sorted(scopes):
            m = re.match(r"gcp:loc:([^:]+):(.+)$", s)
            if m:
                targets.append((m.group(1), m.group(2)))
        recs = {}  # merge key -> record

        def rec(key):
            return recs.setdefault(key, {"surfaces": [], "engine": None, "revisions": [], "registry": None, "ge": None,
                                         "bindings": [], "project": None, "location": None})
        total = max(len(targets), 1)
        for i, (proj, loc) in enumerate(targets):
            if progress:
                progress(i, total, f"Reading {proj} ({loc})")
            if loc != "global":
                self._runtime(creds, proj, loc, rec)
            self._registry(creds, proj, loc, rec)
            if loc in GE_LOCS:
                self._gemini(creds, proj, loc, rec)
        org = cfg.get("org")
        out = [self._to_agent(creds, org, key, r) for key, r in recs.items()]
        if progress:
            progress(total, total, f"Found {len(out)} agents")
        return out

    def _runtime(self, creds, proj, loc, rec):
        base = AI.format(loc=loc)
        engines, err = _paged(creds, f"{base}/v1/projects/{proj}/locations/{loc}/reasoningEngines", "reasoningEngines",
                              {"pageSize": 100})
        for e in engines:
            r = rec(e["name"])
            r.update(engine=e, project=proj, location=loc)
            r["surfaces"].append("agent_runtime")
            st, _, body = _call(creds, "GET", f"{base}/v1beta1/{e['name']}/runtimeRevisions")
            r["revisions"] = (body or {}).get("runtimeRevisions") or [] if st == 200 else []

    def _registry(self, creds, proj, loc, rec):
        agents, err = _paged(creds, f"{REGISTRY}/projects/{proj}/locations/{loc}/agents", "agents", {"pageSize": 100})
        for a in agents:
            ref = ((a.get("attributes") or {}).get(RT_REF) or {}).get("uri") or ""
            key = ref.split("googleapis.com/", 1)[-1] if ref else a.get("agentId") or a["name"]
            r = rec(key)
            r["registry"] = a
            r["project"] = r["project"] or proj
            r["location"] = r["location"] or loc
            if "registry" not in r["surfaces"]:
                r["surfaces"].append("registry")
            if a.get("agentId"):
                st, _, body = _call(creds, "GET", f"{REGISTRY}/projects/{proj}/locations/{loc}/bindings:fetchAvailable",
                                    params={"sourceIdentifier": a["agentId"]})
                if st == 200:
                    r["bindings"] = (body or {}).get("bindings") or []

    def _gemini(self, creds, proj, loc, rec):
        cols, err = _paged(creds, f"{GE}/projects/{proj}/locations/{loc}/collections", "collections")
        for c in cols:
            engines, _ = _paged(creds, f"{GE}/{c['name']}/engines", "engines")
            for en in engines:
                assistants, _ = _paged(creds, f"{GE}/{en['name']}/assistants", "assistants")
                for asst in assistants:
                    agents, _ = _paged(creds, f"{GE}/{asst['name']}/agents", "agents", {"pageSize": 100})
                    for a in agents:
                        eng = (((a.get("adkAgentDefinition") or {}).get("provisionedReasoningEngine") or {})
                               .get("reasoningEngine"))
                        r = rec(eng or a["name"])
                        r["ge"] = a
                        r["project"] = r["project"] or proj
                        r["location"] = r["location"] or loc
                        if "gemini_enterprise" not in r["surfaces"]:
                            r["surfaces"].append("gemini_enterprise")

    def _data_scopes(self, creds, org, identity):
        """Roles granted to the agent's own identity, as '{role} on {resource}'."""
        if not (org and identity.get("distinct") and identity.get("principal") not in (None, "unknown")):
            return []
        res, err = _paged(creds, f"{CAI}/organizations/{org}:searchAllIamPolicies", "results",
                          {"query": f'policy:"{identity["principal"]}"', "pageSize": 100})
        out = []
        for r in res:
            for b in (r.get("policy") or {}).get("bindings") or []:
                if any(identity["principal"] in m for m in b.get("members") or []):
                    out.append(f"{b.get('role')} on {r.get('resource')}")
        return out

    def _to_agent(self, creds, org, key, r):
        e, reg, ge = r["engine"], r["registry"], r["ge"]
        spec = (e or {}).get("spec") or {}
        labels = (e or {}).get("labels") or {}
        card = spec.get("agentCard") or {}
        tools = []
        for sk in list(card.get("skills") or []) + list((reg or {}).get("skills") or []):
            n = sk.get("name") or sk.get("id")
            if n and n not in tools:
                tools.append(n)
        # identity: engine first, then the registry's runtime identity, else Google-managed
        reg_principal = (((reg or {}).get("attributes") or {}).get(RT_IDENT) or {}).get("principal")
        eff = spec.get("effectiveIdentity") or spec.get("serviceAccount")
        if eff:
            ident = _identity_from(eff, "agent_identity" if spec.get("identityType") == "AGENT_IDENTITY" else None)
        elif reg_principal:
            ident = _identity_from(reg_principal)
        else:
            ident = {"type": "google_managed", "principal": "unknown", "distinct": False}
        # models: only env keys that name a model (values of other keys are never read)
        models = []
        for v in ((spec.get("deploymentSpec") or {}).get("env") or []):
            if "MODEL" in str(v.get("name", "")).upper() and not SECRET_RE.search(str(v.get("name"))) and v.get("value"):
                models.append(v["value"])
        mcp = [b["target"]["identifier"] for b in r["bindings"] if str((b.get("target") or {}).get("identifier", "")).startswith("urn:mcp:")]
        agents_dep = [b["target"]["identifier"] for b in r["bindings"] if str((b.get("target") or {}).get("identifier", "")).startswith("urn:agent:")]
        data_scopes = self._data_scopes(creds, org, ident)
        systems = []
        for s in data_scopes:
            m = re.search(r"//([a-z]+)\.googleapis\.com", s)
            if m and m.group(1) not in systems:
                systems.append(m.group(1))
        systems += [_short(t.replace(":", "/")) for t in mcp if _short(t.replace(":", "/")) not in systems]
        # status
        if e:
            revs = r["revisions"]
            status = "ARCHIVED" if revs and not any(x.get("state") == "ACTIVE" for x in revs) else "DEPLOYED"
        elif ge and not reg:
            status = {"ENABLED": "ACTIVE", "DISABLED": "INACTIVE", "SUSPENDED": "SUSPENDED", "PRIVATE": "DRAFT"}.get(
                ge.get("state"), "ERROR" if "FAILED" in str(ge.get("state")) else str(ge.get("state") or "unknown"))
        elif reg:
            status = "READY"
        else:
            status = "unknown"
        # versions
        revs = sorted(r["revisions"], key=lambda x: x.get("createTime", ""), reverse=True)
        if revs:
            versions = [{"version": _short(x["name"]), "created": x.get("createTime"), "state": x.get("state")} for x in revs]
            active = [x for x in revs if x.get("state") == "ACTIVE"]
            version = _short((active or revs)[0]["name"])
        elif e:
            version, versions = e.get("etag") or "1", [{"version": e.get("etag") or "1", "created": e.get("createTime")}]
        elif reg:
            version, versions = reg.get("version") or "1", [{"version": reg.get("version") or "1", "created": reg.get("createTime")}]
        else:
            version, versions = "1", [{"version": ge.get("updateTime") or "1", "created": ge.get("createTime")}]
        src = e or reg or ge
        name = (e or {}).get("displayName") or (reg or {}).get("displayName") or (ge or {}).get("displayName") or _short(key)
        iface = (((reg or {}).get("protocols") or [{}])[0].get("interfaces") or [{}])[0].get("url")
        loc, proj = r["location"], r["project"]
        url = iface or (f"https://console.cloud.google.com/vertex-ai/agents/locations/{loc}/agent-engines/{_short(key)}?project={proj}"
                        if e else None)
        criteria = {"identity_type": ident["type"], "default_service_agent": ident["type"] == "service_agent_default"}
        if spec.get("agentFramework"):
            criteria["framework"] = spec["agentFramework"]
        if labels.get("env"):
            criteria["env"] = labels["env"]
        if ge:
            criteria["sharing"] = (ge.get("sharingConfig") or {}).get("scope")
        return SourceAgent(
            platform="google", native_id=key, name=name,
            description=(e or {}).get("description") or (reg or {}).get("description") or (ge or {}).get("description") or "",
            version=version, owner=labels.get("owner") or labels.get("team") or "unknown", status=status,
            last_updated=(src or {}).get("updateTime"), url=url, provider="Google Cloud", tools=tools,
            data_scopes=data_scopes, systems=systems, value_limits={}, criteria=criteria, identity=ident, versions=versions,
            dependencies={"models": models, "mcp_servers": mcp, "agents": agents_dep},
            extra={"surfaces": r["surfaces"], "project": proj, "location": loc, "registry_agent": (reg or {}).get("name"),
                   "gemini_enterprise_agent": (ge or {}).get("name"), "framework": spec.get("agentFramework")},
            raw={"engine": _scrub_engine(e) if e else None, "registry": reg, "gemini_enterprise": ge, "revisions": revs})

    # ------------------------------------------------------------------ sample and legacy replay
    def _sample_discover(self):
        extra = [SourceAgent(platform="google", **a) for a in _sample().get("agents", [])]
        return self.list_agents() + extra

    def list_agents(self):
        """Replay: fixtures/google/ holds Agent Registry v1 agents + bindings and an ACT overlay (data the API doesn't carry)."""
        if live_enabled("google"):
            import os
            cfg = json.loads(os.environ.get("ACT_GOOGLE_CFG", "{}"))
            creds = {"access_token": os.environ.get("ACT_GOOGLE_TOKEN", "")}
            ids = [n["id"] for n in self._flat(self.list_scopes(cfg, creds))]
            return self.discover(cfg, creds, ids)
        agents = load_fixture("google/agent-registry-agents.json").get("agents", [])
        overlay = load_fixture("google/act-overlay.json").get("overlay", {})
        out = []
        for a in agents:
            ov = overlay.get(a["agentId"], {})
            ref = ((a.get("attributes") or {}).get(RT_REF) or {}).get("uri", "")
            principal = ((a.get("attributes") or {}).get(RT_IDENT) or {}).get("principal")
            ident = ov.get("identity") or _identity_from(principal)
            iface = ((a.get("protocols") or [{}])[0].get("interfaces") or [{}])[0].get("url")
            out.append(SourceAgent(
                platform="google", native_id=_short(ref) or a["agentId"], name=a["displayName"],
                description=a.get("description") or "", version=a.get("version") or "1", owner=ov.get("owner") or "unknown",
                status="READY", last_updated=a.get("updateTime"), url=iface, provider="Google Cloud",
                tools=[s.get("id") or s.get("name") for s in a.get("skills") or []],
                data_scopes=list(ov.get("data_scopes") or []), systems=list(ov.get("systems") or []),
                value_limits=dict(ov.get("value_limits") or {}), criteria=dict(ov.get("criteria") or {}), identity=dict(ident),
                versions=[{"version": a.get("version") or "1", "created": a.get("createTime")}],
                extra={"surfaces": ["registry"], "registry_agent": a["name"]}, raw=a))
        return out

    @staticmethod
    def _flat(tree):
        for n in tree:
            yield n
            yield from GoogleAdapter._flat(n.get("children") or [])
