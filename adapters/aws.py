"""AWS adapter: Bedrock AgentCore Runtimes (control plane), their gateways, tags and IAM execution roles.

Real APIs do not carry tools, data scopes or value limits as first-class fields on a Runtime, so they are derived:
  - tools          from the Gateway targets' tool schemas (GetAgentRuntime env GATEWAY_URL -> gateway id)
  - data_scopes    from the Resource ARNs in the execution role's IAM policies
  - value_limits   from `act:limit:<tool>=<amount><CCY>` resource tags
  - owner          from the `owner` tag
Everything the APIs cannot provide is "unknown" (scalars) or empty (lists), never invented. Raw payloads pass redact();
environment variable values are never kept (keys only).

All network calls go through the module-level `_http(method, url, **kw) -> (status, headers, json)`.
Sample mode (cfg["sample"]) never calls it: it replays fixtures/aws/ (+ discovery-only runtimes from the sample tenant).
`list_agents()` is the replay path used by jobs/intake.py.
"""
import copy
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import quote, unquote, urlparse

from .base import AdapterBase, SourceAgent, fixtures_dir, live_enabled, load_fixture

READ_VERBS = ("Get", "List", "Describe", "Query", "Scan", "Search", "BatchGet", "Lookup", "Head", "Select", "Read")
DATA_SERVICES = {"s3", "dynamodb", "secretsmanager", "rds", "redshift", "glue", "athena", "bedrock-agentcore"}
TEMPLATE_FIX = "Click Update stack in AWS (CloudFormation, about a minute) to refresh the ACT role, then check again."
PROVIDER = "AWS Bedrock AgentCore"


class Dep(dict):
    """A dependency record ({"name": ...}) as backend.normalize expects. It also equals its plain name, so the
    adapter contract's `"model-id" in agent.dependencies["models"]` holds."""

    def __init__(self, name):
        super().__init__(name=str(name))

    def __eq__(self, other):
        return self["name"] == other if isinstance(other, str) else dict.__eq__(self, other)

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = None


# ------------------------------------------------------------------ transport
def _xml_to_obj(el):
    kids = list(el)
    tag = el.tag.split("}")[-1]
    if not kids:
        return tag, (el.text or "").strip()
    out = {}
    for k in kids:
        t, v = _xml_to_obj(k)
        if t in out:
            out[t] = out[t] if isinstance(out[t], list) else [out[t]]
            out[t].append(v)
        else:
            out[t] = v
    return tag, out


def _http(method, url, **kw):
    """The only place that touches the network. -> (status, headers, parsed body)."""
    import requests
    r = requests.request(method, str(url), headers=kw.get("headers"), data=kw.get("data"), timeout=30)
    try:
        body = r.json()
    except ValueError:
        try:
            import xml.etree.ElementTree as ET
            t, v = _xml_to_obj(ET.fromstring(r.text))
            body = {t: v}
        except Exception:
            body = {"message": r.text[:300]}
    return r.status_code, dict(r.headers), body


def _sig(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _sigv4(method, url, body, creds, service, region, extra=None):
    """Minimal AWS Signature V4 headers for one request."""
    u = urlparse(url)
    now = _dt.datetime.now(_dt.timezone.utc)
    amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload = (body or "").encode() if isinstance(body, str) else (body or b"")
    hdrs = {"host": u.netloc, "x-amz-date": amz, **(extra or {})}
    if creds.get("session_token"):
        hdrs["x-amz-security-token"] = creds["session_token"]
    hdrs = {k.lower(): v for k, v in hdrs.items()}
    names = sorted(hdrs)
    q = "&".join(sorted(u.query.split("&"))) if u.query else ""
    canon = "\n".join([method.upper(), quote(u.path or "/", safe="/%"), q,
                       "".join(f"{n}:{hdrs[n].strip()}\n" for n in names), ";".join(names),
                       hashlib.sha256(payload).hexdigest()])
    scope = f"{day}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    k = _sig(_sig(_sig(_sig(("AWS4" + creds.get("secret_access_key", "")).encode(), day), region), service),
             "aws4_request")
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    hdrs["authorization"] = (f"AWS4-HMAC-SHA256 Credential={creds.get('access_key_id', '')}/{scope}, "
                             f"SignedHeaders={';'.join(names)}, Signature={sig}")
    return hdrs


def _call(method, service, region, path, creds, query=None, body=None, form=None):
    """One signed call -> (status, body). Network errors and throttling never raise."""
    host = "iam.amazonaws.com" if service == "iam" else f"{service}.{region}.amazonaws.com"
    url = f"https://{host}{path}"
    if query:
        url += "?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in query.items() if v is not None)
    data, extra = None, {}
    if form is not None:
        data = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in {**form, "Version": "2010-05-08"}.items())
        extra = {"content-type": "application/x-www-form-urlencoded; charset=utf-8"}
    elif body is not None:
        data = json.dumps(body)
        extra = {"content-type": "application/json"}
    sign_region = "us-east-1" if service == "iam" else region
    for attempt in range(3):
        try:
            hdrs = _sigv4(method, url, data, creds or {}, service, sign_region, extra)
            st, h, js = _http(method, url, headers=hdrs, data=data)
        except (OSError, ValueError) as e:  # network error: degrade, never raise
            return 0, {"message": str(e)}
        if st == 429 and attempt < 2:
            time.sleep(min(float((h or {}).get("Retry-After") or (h or {}).get("retry-after") or 1) , 5))
            continue
        return st, js if isinstance(js, (dict, list)) else {}
    return st, js


def _deep(o, key):
    """First value for `key` anywhere in a nested structure (IAM XML wraps results; recordings do not)."""
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _deep(v, key)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _deep(v, key)
            if r is not None:
                return r
    return None


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, dict) and "member" in v:
        v = v["member"]
    return v if isinstance(v, list) else [v]


def _denied(st, body):
    t = str((body or {}).get("__type") or (body or {}).get("code") or "")
    return st in (401, 403) or "AccessDenied" in t or "UnauthorizedOperation" in t


def _missing_action(body, default):
    m = re.search(r"perform:\s*([A-Za-z0-9-]+:[A-Za-z*]+)", str((body or {}).get("message") or ""))
    return m.group(1) if m else default


def _fix_missing(action):
    return f"The ACT role is missing {action}. {TEMPLATE_FIX}"


# ------------------------------------------------------------------ pure helpers
def _statements(policy_docs):
    out = []
    for d in policy_docs:
        if isinstance(d, str):
            try:
                d = json.loads(d if d.lstrip().startswith("{") else unquote(d))
            except ValueError:
                continue
        st = (d or {}).get("Statement") or []
        out += [st] if isinstance(st, dict) else list(st)
    return out


def _actions(stmts):
    acts = []
    for s in stmts:
        if s.get("Effect", "Allow") != "Allow":
            continue
        acts += _as_list(s.get("Action"))
    return list(dict.fromkeys(acts))


def write_actions(stmts):
    """Granted actions that can invoke or change something."""
    bad = []
    for a in _actions(stmts):
        verb = a.split(":", 1)[-1]
        if a == "*" or verb == "*" or not verb.startswith(READ_VERBS):
            bad.append(a)
    return bad


def data_scopes(stmts):
    """Resource names (not whole ARNs) of data stores the role can touch."""
    out = []
    for s in stmts:
        if s.get("Effect", "Allow") != "Allow":
            continue
        for r in _as_list(s.get("Resource")):
            parts = str(r).split(":", 5)
            if len(parts) < 6 or parts[0] != "arn" or parts[2] not in DATA_SERVICES:
                continue
            name = parts[5]
            name = name.split("/", 1)[1] if name.startswith(("table/", "secret/")) else name
            if name and name != "*":
                out.append(name)
    return list(dict.fromkeys(out))


def _tool_names(targets):
    tools, systems = [], []
    for it in _as_list((targets or {}).get("items") if isinstance(targets, dict) else targets):
        tc = (it.get("targetConfiguration") or {}).get("mcp") or {}
        for t in ((tc.get("lambda") or {}).get("toolSchema") or {}).get("inlinePayload") or []:
            if t.get("name"):
                tools.append(t["name"])
        ep = (tc.get("mcpServer") or {}).get("endpoint")
        if ep:
            systems.append(urlparse(ep).hostname or ep)
    return list(dict.fromkeys(tools)), list(dict.fromkeys(systems))


def _limits(tags):
    out = {}
    for k, v in (tags or {}).items():
        m = re.fullmatch(r"act:limit:(.+)", k)
        n = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]{3})?\s*", str(v))
        if m and n:
            amt = float(n.group(1))
            out[m.group(1)] = {"amount": int(amt) if amt == int(amt) else amt, "currency": (n.group(2) or "USD").upper()}
    return out


def _owner(tags):
    for k in ("owner", "Owner", "team", "CreatedBy"):
        if (tags or {}).get(k):
            return tags[k]
    return None


def _redact(o):
    try:
        from backend.normalize_span import redact
        return redact(o)
    except Exception:  # pragma: no cover
        return o


def _gateway_id(env):
    m = re.match(r"https?://([^./]+)\.gateway\.bedrock-agentcore\.", str((env or {}).get("GATEWAY_URL") or ""))
    return m.group(1) if m else None


def _models(env):
    return [str(v) for k, v in (env or {}).items() if "MODEL" in k.upper() and v]


def _build(summary, get=None, tags=None, stmts=None, targets=None, overlay=None, rec=None, shared=None,
           native_id=None, issues=None):
    """One SourceAgent from whatever the APIs returned. `get` is None when GetAgentRuntime failed."""
    overlay = overlay or {}
    g = get or {}
    arn = summary.get("agentRuntimeArn") or g.get("agentRuntimeArn")
    rid = summary.get("agentRuntimeId") or g.get("agentRuntimeId")
    parts = (arn or "").split(":")
    region, acct = (parts[3], parts[4]) if len(parts) > 5 else ("", "")
    env = g.get("environmentVariables") or {}
    tools, gw_systems = _tool_names(targets)
    tools = list(dict.fromkeys(tools + [t for t in overlay.get("tools", [])]))
    limits = {**_limits(tags), **(overlay.get("value_limits") or {})}
    role = g.get("roleArn")
    writes = write_actions(stmts or [])
    criteria = dict(overlay.get("criteria") or {})
    if writes:
        criteria["write_actions"] = writes
    if role and not shared:
        identity = {"type": "iam_role", "principal": role, "distinct": True}
        wi = (g.get("workloadIdentityDetails") or {}).get("workloadIdentityArn")
        if wi:
            identity["workload_identity"] = wi
    else:
        identity = {}  # no identity of its own (shared role) or not readable
    version = str(g.get("agentRuntimeVersion") or summary.get("agentRuntimeVersion") or "unknown")
    updated = g.get("lastUpdatedAt") or summary.get("lastUpdatedAt")
    raw_get = copy.deepcopy(g)
    if "environmentVariables" in raw_get:
        raw_get["environmentVariables"] = {k: "[REDACTED]" for k in env}
    prov = [p for p in (rec or {}).get("provenance", []) if isinstance(p, dict)]
    return SourceAgent(
        platform="aws", native_id=native_id or arn or rid, name=summary.get("agentRuntimeName") or g.get("agentRuntimeName") or rid,
        description=summary.get("description") or g.get("description") or "", version=version,
        owner=_owner(tags) or "unknown", status=g.get("status") or summary.get("status") or "unknown",
        last_updated=updated,
        url=(f"https://{region}.console.aws.amazon.com/bedrock-agentcore/home?region={region}#/agents/{rid}" if region else None),
        provider=PROVIDER, tools=tools, data_scopes=data_scopes(stmts or []),
        systems=list(dict.fromkeys(list(overlay.get("systems") or []) + gw_systems)), value_limits=limits,
        criteria=criteria, identity=identity,
        versions=[{"version": version, "created": updated}] if get or summary.get("agentRuntimeVersion") else [],
        dependencies={"models": [Dep(m) for m in (_models(env) or ([] if get else ["unknown"]))],
                      "mcp_servers": [Dep(env["GATEWAY_URL"])] if env.get("GATEWAY_URL") else []},
        extra={"kind": "runtime", "arn": arn, "runtime_id": rid, "account": acct, "region": region,
               "protocol": (g.get("protocolConfiguration") or {}).get("serverProtocol"),
               "network_mode": (g.get("networkConfiguration") or {}).get("networkMode"),
               "env_var_keys": sorted(env), "role_arn": role,
               "registry_status": (rec or {}).get("status"), "registry_record": (rec or {}).get("recordId"),
               "details_readable": bool(get), "gaps": list(issues or []),
               **({"shared_role": role} if role and shared else {})},
        raw=_redact({"runtime": raw_get or dict(summary), "registry": rec, "provenance": prov}))


# ------------------------------------------------------------------ adapter
class AwsAdapter(AdapterBase):
    platform = "aws"

    # ---- legacy replay (jobs/intake.py)
    def _replay_docs(self):
        runtimes = load_fixture("aws/list-agent-runtimes.json")
        registry = load_fixture("aws/list-discoverable-registry-records.json")
        p = fixtures_dir() / "aws" / "runtime-details.json"
        details = json.loads(p.read_text()).get("runtimes", {}) if p.exists() else {}
        return runtimes, registry, details

    @staticmethod
    def _from_detail(summary, d, overlay=None, rec=None, native_id=None, shared=None):
        d = d or {}
        shared = shared if shared is not None else (d.get("roleUsage") or {}).get("distinct") is False
        return _build(summary, d.get("getAgentRuntime"), d.get("tags"),
                      _statements([(d.get("rolePolicy") or {}).get("PolicyDocument")] if d.get("rolePolicy") else []),
                      d.get("gatewayTargets"), overlay, rec, shared, native_id)

    def list_agents(self):
        if live_enabled("aws"):
            cfg = {"sample": False, "account_id": os.environ.get("ACT_AWS_ACCOUNT_ID", ""),
                   "regions": [r for r in os.environ.get("ACT_AWS_REGIONS", "us-east-1").split(",") if r]}
            creds = {"access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", ""),
                     "secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
                     "session_token": os.environ.get("AWS_SESSION_TOKEN", "")}
            return self.discover(cfg, creds, [])
        runtimes, registry, details = self._replay_docs()
        recs = {p.get("sourceId"): r for r in registry.get("registryRecords", []) for p in r.get("provenance", [])}
        out = []
        for rt in runtimes.get("agentRuntimes", []):
            out.append(self._from_detail(rt, details.get(rt["agentRuntimeId"]), rt.get("metadata"),
                                         recs.get(rt.get("agentRuntimeArn")), native_id=rt["agentRuntimeId"]))
        return out

    # ---- connections contract
    def auth_methods(self):
        return ["oidc_role_quick_create",   # primary: CloudFormation role that trusts this Domino's OIDC issuer, no secrets
                "external_id_role"]         # fallback: same template, ExternalId trust

    @staticmethod
    def _sample(cfg):
        return bool((cfg or {}).get("sample"))

    @staticmethod
    def _home(cfg):
        return ((cfg or {}).get("regions") or ["us-east-1"])[0]

    def test_connection(self, cfg, creds):
        if self._sample(cfg):
            from backend.connections import sample
            return sample.permissions("aws", recheck=True)
        region = self._home(cfg)
        items = []
        st, body = _call("POST", "bedrock-agentcore-control", region, "/runtimes/", creds,
                         query={"maxResults": 1}, body={})
        action = "bedrock-agentcore:ListAgentRuntimes"
        if 200 <= st < 300:
            items.append({"name": "Read AgentCore runtimes", "detail": action, "required": True, "ok": True, "fix": None})
        elif _denied(st, body):
            items.append({"name": "Read AgentCore runtimes", "detail": action, "required": True, "ok": False,
                          "fix": _fix_missing(_missing_action(body, action))})
        else:
            items.append({"name": "Read AgentCore runtimes", "detail": action, "required": True, "ok": False,
                          "fix": f"AWS answered {st or 'with a network error'} in {region}. AgentCore may not be offered in "
                                 f"that Region, or AWS is unavailable; pick another Region or try again."})
        role = (cfg or {}).get("role_arn") or ""
        role_name = role.rsplit("/", 1)[-1] or "ACT-ReadOnly"
        stmts, st2, body2 = self._role_statements(role_name, creds)
        if 200 <= st2 < 300:
            items.append({"name": "Read IAM role policies", "detail": "iam:ListRolePolicies, iam:GetRolePolicy",
                          "required": False, "ok": True, "fix": None})
            for a in write_actions(stmts):
                items.append({"name": f"Read-only check: {a}",
                              "detail": f"ACT asked for read-only; this role can also {a}",
                              "required": False, "ok": False,
                              "fix": f"Remove {a} from the ACT role, or Update stack to the read-only template, then check again."})
        else:
            items.append({"name": "Read IAM role policies", "detail": "iam:ListRolePolicies, iam:GetRolePolicy",
                          "required": False, "ok": False,
                          "fix": _fix_missing(_missing_action(body2, "iam:ListRolePolicies"))})
        return items

    def _role_statements(self, role_name, creds):
        st, body = _call("POST", "iam", "us-east-1", "/", creds, form={"Action": "ListRolePolicies", "RoleName": role_name})
        if not 200 <= st < 300:
            return [], st, body
        names = _as_list(_deep(body, "PolicyNames")) or ([_deep(body, "PolicyName")] if _deep(body, "PolicyName") else [])
        docs = []
        for n in dict.fromkeys(names):
            s2, b2 = _call("POST", "iam", "us-east-1", "/", creds,
                           form={"Action": "GetRolePolicy", "RoleName": role_name, "PolicyName": n})
            if 200 <= s2 < 300 and _deep(b2, "PolicyDocument") is not None:
                docs.append(_deep(b2, "PolicyDocument"))
        return _statements(docs), st, body

    # ---- scopes
    def list_scopes(self, cfg, creds):
        def node(i, label, kind, children):
            return {"id": i, "label": label, "name": label, "kind": kind, "selected": True, "children": children}
        if self._sample(cfg):
            from backend.connections import sample
            out = []
            for a in sample.tree("aws"):
                out.append(node(a["id"], a["label"], "account",
                                [node(c["id"], c["label"], "region", []) for c in a.get("children", [])]))
            return out
        acct = (cfg or {}).get("account_id")
        if not acct:
            st, body = _call("POST", "sts", "us-east-1", "/", creds, form={"Action": "GetCallerIdentity"})
            acct = (_deep(body, "Account") or "") if 200 <= st < 300 else ""
        regions = list((cfg or {}).get("regions") or [])
        st, body = _call("POST", "account", self._home(cfg), "/listRegions", creds,
                         body={"RegionOptStatusContains": ["ENABLED", "ENABLED_BY_DEFAULT"]})
        if 200 <= st < 300 and isinstance(body.get("Regions"), list):
            avail = [r["RegionName"] for r in body["Regions"] if r.get("RegionName")]
            regions = [r for r in regions if r in avail] or regions or avail
        regions = regions or ["us-east-1"]
        return [node(f"aws:acct:{acct}", f"AWS account {acct}", "account",
                     [node(f"aws:acct:{acct}:{r}", r, "region", []) for r in regions])]

    # ---- discovery
    @staticmethod
    def _scope_pairs(scopes, cfg):
        pairs = []
        for s in scopes or []:
            m = re.fullmatch(r"aws:acct:([\d-]+):([a-z0-9-]+)", str(s))
            if m:
                pairs.append((m.group(1).replace("-", ""), m.group(2)))
        if not pairs:
            pairs = [((cfg or {}).get("account_id", ""), r) for r in (cfg or {}).get("regions") or ["us-east-1"]]
        return list(dict.fromkeys(pairs))

    def _crawl(self, cfg, creds, scopes, progress=None):
        """-> (agents, issues[{reason, fix}])"""
        issues, agents = [], []
        pairs = self._scope_pairs(scopes, cfg)
        for n, (acct, region) in enumerate(pairs):
            listed, token = [], None
            while True:
                st, body = _call("POST", "bedrock-agentcore-control", region, "/runtimes/", creds,
                                 query={"maxResults": 100, "nextToken": token}, body={})
                if not 200 <= st < 300:
                    if _denied(st, body):
                        a = _missing_action(body, "bedrock-agentcore:ListAgentRuntimes")
                        issues.append({"reason": f"ACT cannot list AgentCore runtimes in {region}: missing {a}.",
                                       "fix": _fix_missing(a)})
                    elif st == 404 or st == 0:
                        issues.append({"reason": f"AgentCore is not reachable in {region}; its agents are not listed.",
                                       "fix": "AgentCore may not be offered in this Region or the Region is disabled; deselect it, or check the Region is enabled."})
                    else:
                        issues.append({"reason": f"AWS returned {st} listing runtimes in {region}.",
                                       "fix": "Check again in a few minutes; AWS may be throttling or unavailable."})
                    break
                listed += body.get("agentRuntimes") or []
                token = body.get("nextToken")
                if not token:
                    break
            for rt in listed:
                agents.append(self._read_one(rt, region, creds, issues))
            if progress:
                progress(n + 1, len(pairs), f"{acct} / {region}: {len(listed)} runtimes")
        # a role used by more than one runtime is not a distinct identity
        counts = {}
        for a in agents:
            r = a.extra.get("role_arn")
            if r:
                counts[r] = counts.get(r, 0) + 1
        for a in agents:
            r = a.extra.get("role_arn")
            if r and counts[r] > 1:
                a.identity = {}
                a.extra["shared_role"] = r
        return agents, issues

    def _read_one(self, rt, region, creds, issues):
        rid, arn, gaps = rt["agentRuntimeId"], rt["agentRuntimeArn"], []
        st, get = _call("GET", "bedrock-agentcore-control", region, f"/runtimes/{quote(rid, safe='')}/", creds)
        if not (200 <= st < 300 and isinstance(get, dict) and get.get("agentRuntimeId")):
            gaps.append("details")
            issues.append({"reason": f"Could not read the details of {rt.get('agentRuntimeName', rid)} (HTTP {st}); its role, "
                                     f"tools and model are unknown.",
                           "fix": "Make sure the ACT role allows bedrock-agentcore:GetAgentRuntime (" + TEMPLATE_FIX.lower() + ")"
                           if _denied(st, get) else "It may have been deleted during the scan; ACT marks it Missing on the next crawl."})
            return _build(rt, None, None, None, None, native_id=arn, issues=gaps)
        stt, tg = _call("GET", "bedrock-agentcore-control", region, f"/tags/{quote(arn, safe='')}", creds)
        tags = tg.get("tags") if 200 <= stt < 300 and isinstance(tg, dict) else None
        if tags is None:
            gaps.append("tags")
        stmts = []
        if get.get("roleArn"):
            stmts, sti, _ = self._role_statements(get["roleArn"].rsplit("/", 1)[-1], creds)
            if not 200 <= sti < 300:
                gaps.append("role policy")
        targets, gid = None, _gateway_id(get.get("environmentVariables"))
        if gid:
            stg, tgt = _call("GET", "bedrock-agentcore-control", region, f"/gateways/{quote(gid, safe='')}/targets/", creds)
            if 200 <= stg < 300:
                targets = tgt
            else:
                gaps.append("gateway targets")
        else:
            gaps.append("gateway")
        if tags is None:
            issues.append({"reason": f"Owner and value limits of {rt['agentRuntimeName']} are unknown (tags not readable).",
                           "fix": "Allow bedrock-agentcore:ListTagsForResource on the ACT role and tag the runtime with owner and act:limit:<tool>."})
        if "gateway targets" in gaps or "gateway" in gaps:
            issues.append({"reason": f"Tools of {rt['agentRuntimeName']} are unknown (no readable gateway targets).",
                           "fix": "Allow bedrock-agentcore:ListGatewayTargets, or set GATEWAY_URL on the runtime so ACT can find its gateway."})
        if "role policy" in gaps:
            issues.append({"reason": f"Data scopes of {rt['agentRuntimeName']} are unknown (role policies not readable).",
                           "fix": "Allow iam:ListRolePolicies and iam:GetRolePolicy on the ACT role. " + TEMPLATE_FIX})
        return _build(rt, get, tags, stmts, targets, native_id=arn, issues=gaps)

    def discover(self, cfg, creds, scopes, progress=None):
        if self._sample(cfg):
            return self._sample_agents()
        agents, issues = self._crawl(cfg, creds, scopes, progress)
        self._last_issues = issues
        return agents

    def _sample_agents(self):
        from backend.connections import sample
        out = self.list_agents_replay()
        for d in sample.tenant("aws").get("agents") or []:
            g = d.get("getAgentRuntime") or {}
            out.append(self._from_detail(g, d, native_id=g.get("agentRuntimeArn")))
        return out

    def list_agents_replay(self):
        saved = os.environ.pop("ACT_LIVE_AWS", None)
        try:
            return self.list_agents()
        finally:
            if saved is not None:
                os.environ["ACT_LIVE_AWS"] = saved

    # ---- coverage (PO-3.5)
    def coverage(self, cfg, creds, scopes=None):
        static = [{"reason": "Only AgentCore Runtimes are inventoried. AgentCore harnesses and classic Bedrock Agents are not read yet.",
                   "fix": "Register them in the ACT import review by hand, or ask for the harness and Bedrock Agents adapter."},
                  {"reason": "Agents in AWS accounts or Regions you did not select are not scanned.",
                   "fix": "Connect each additional account (or the whole AWS Organization) from the Connect a provider page."}]
        if self._sample(cfg):
            return static
        _, issues = self._crawl(cfg, creds, scopes if scopes is not None else None)
        return issues + static
