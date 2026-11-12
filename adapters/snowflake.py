"""Snowflake adapter: Cortex Agents through the SQL API (SHOW AGENTS, DESCRIBE AGENT, SHOW VERSIONS, SHOW GRANTS).

Read-only: ACT never runs or changes agents. Auth is Workload Identity Federation with the Domino token (primary) or
OAuth / key pair (fallback); `creds` is {"token", "token_type"}.

Mapping notes (see research/providers/snowflake.md section 3): a Cortex Agent has no identity of its own (tools run
under the caller's roles), no status field and no value limits. Unreadable values are "unknown" (scalars) or empty
(lists), never invented. `agent_spec` free text (instructions, filters, auth headers, URLs) is never kept: raw holds a
structural projection that is also scrubbed for keys and tokens.

All network calls go through the module-level `_http(method, url, **kw) -> (status, headers, json)`.
Sample mode (cfg["sample"]) never calls it. Snowflake is not part of jobs/intake.py.
"""
import datetime as _dt
import hashlib
import json
import re
import time

from .base import AdapterBase, SourceAgent

PROVIDER = "Snowflake Cortex"
READ_PRIVS = {"USAGE", "MONITOR", "SELECT", "REFERENCES", "READ", "IMPORTED PRIVILEGES"}
GRANT_FIX = ("ACT can't see your agents yet. Run the grant snippet again as ACCOUNTADMIN "
             "(GRANT MONITOR ON ALL AGENTS IN SCHEMA <db>.<schema> TO ROLE ACT_READER; ACT regenerates it for you).")
ADMIN_FIX = ("Run this as ACCOUNTADMIN (first line USE ROLE ACCOUNTADMIN), or ask your Snowflake admin "
             "(use Send to admin).")


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
def _http(method, url, **kw):
    """The only place that touches the network. -> (status, headers, json)."""
    import requests
    r = requests.request(method, str(url), headers=kw.get("headers"), json=kw.get("json"),
                         params=kw.get("params"), timeout=kw.get("timeout", 60))
    try:
        body = r.json()
    except ValueError:
        body = {"message": r.text[:300]}
    return r.status_code, dict(r.headers), body


def _host(cfg):
    return (cfg.get("host") or f"{cfg.get('account', '')}.snowflakecomputing.com").replace("_", "-")


def _headers(creds):
    tok, tt = (creds or {}).get("token", ""), (creds or {}).get("token_type") or "OAUTH"
    bearer = f"WIF.OIDC.{tok}" if tt == "WORKLOAD_IDENTITY_FEDERATION" else tok
    return {"Authorization": f"Bearer {bearer}", "X-Snowflake-Authorization-Token-Type": tt,
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ACT/1.0"}


def _send(method, url, creds, **kw):
    """One request with 429 backoff (honours Retry-After). Never raises: -> (status, body)."""
    st, h, body = 0, {}, {"message": "no response"}
    for attempt in range(3):
        try:
            st, h, body = _http(method, url, headers=_headers(creds), **kw)
        except (OSError, ValueError) as e:
            return 0, {"message": str(e)}
        if st == 429 and attempt < 2:
            ra = {k.lower(): v for k, v in (h or {}).items()}.get("retry-after")
            time.sleep(min(float(ra or 1), 5))
            continue
        break
    return st, body if isinstance(body, dict) else {}


def _cols(body):
    return [c["name"].lower() for c in (body.get("resultSetMetaData") or {}).get("rowType") or []]


def _rows(body):
    cols = _cols(body)
    return [dict(zip(cols, r)) for r in body.get("data") or []]


def _sql(cfg, creds, statement):
    """Run one statement and gather every partition. -> (rows, error) where error is None or
    {"status", "code", "message"}."""
    base = f"https://{_host(cfg)}/api/v2/statements"
    payload = {"statement": statement, "timeout": 60, "role": cfg.get("role"), "warehouse": cfg.get("warehouse")}
    st, body = _send("POST", base, creds, json={k: v for k, v in payload.items() if v})
    polls = 0
    while st == 202 and polls < 30:  # still running: poll the status URL
        polls += 1
        time.sleep(1)
        st, body = _send("GET", f"https://{_host(cfg)}{body.get('statementStatusUrl') or '/api/v2/statements/' + str(body.get('statementHandle'))}", creds)
    if st != 200:
        return [], {"status": st, "code": str(body.get("code") or ""), "message": str(body.get("message") or "")}
    rows = _rows(body)
    handle = body.get("statementHandle")
    parts = (body.get("resultSetMetaData") or {}).get("partitionInfo") or []
    for n in range(1, len(parts)):
        stp, bp = _send("GET", f"{base}/{handle}?partition={n}", creds)
        if stp != 200:
            return rows, {"status": stp, "code": str(bp.get("code") or ""), "message": f"partition {n} failed"}
        rows += [dict(zip(_cols(bp) or _cols(body), r)) for r in bp.get("data") or []]
    return rows, None


def _fix_for(err, what="this"):
    code, st, msg = err.get("code", ""), err.get("status", 0), (err.get("message") or "").lower()
    if code == "003001" or "insufficient privileges" in msg:
        return ADMIN_FIX
    if st == 401:
        return ("Snowflake didn't accept ACT's Domino identity. The App must run as the service account that the "
                "setup SQL trusts; re-generate the SQL, or ask your Snowflake admin to switch to key pair.")
    if code == "390422":
        return "Your account restricts IPs. Add the Domino egress IPs ACT shows to a network policy on ACT_SVC."
    if code == "390189":
        return "Re-run the setup SQL; ACT_READER wasn't granted to ACT_SVC."
    if code == "000606":
        return "ACT_WH is missing or suspended without AUTO_RESUME; re-run the setup SQL."
    if code == "002003":
        return "ACT's role has no access to that object. " + GRANT_FIX
    if st == 429 or code == "000625":
        return "Snowflake asked ACT to slow down. Check again in a minute."
    return f"Snowflake answered {st or 'with a network error'} {code}. Check again, or ask your Snowflake admin."


# ------------------------------------------------------------------ pure helpers
_SCRUB = [re.compile(r"(?i)bearer\s+\S+"), re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"), re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
          re.compile(r"(?i)([?&;]\s*)?(token|api[_-]?key|password|secret)=[^&\s\"']+")]


def _scrub(o):
    if isinstance(o, str):
        if "token=" in o.lower():
            return "[REDACTED]"
        for p in _SCRUB:
            o = p.sub("[REDACTED]", o)
        return o
    if isinstance(o, dict):
        return {k: _scrub(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_scrub(v) for v in o]
    return o


def _redact(o):
    try:
        from backend.normalize_span import redact
        o = redact(o)
    except Exception:  # pragma: no cover
        pass
    return _scrub(o)


def _iso(v):
    try:
        return _dt.datetime.fromtimestamp(float(v), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return v or None


def _display(row):
    try:
        return json.loads(row.get("profile") or "{}").get("display_name") or row["name"]
    except (ValueError, AttributeError):
        return row["name"]


def write_grants(rows):
    """Privileges granted to the role that can invoke, modify or operate something."""
    out = []
    for r in rows:
        p = str(r.get("privilege") or "").upper()
        if p and p not in READ_PRIVS:
            out.append(f"{p} on {r.get('granted_on', 'object')} {r.get('name', '')}".strip())
    return list(dict.fromkeys(out))


def _project_spec(spec):
    """Structural projection of agent_spec: no instructions, filters, descriptions, URLs or auth headers."""
    res = spec.get("tool_resources") or {}
    keep = ("semantic_view", "search_service", "identifier", "type")
    return {"models": spec.get("models") or {},
            "orchestration": {"budget": (spec.get("orchestration") or {}).get("budget"),
                              "tool_not_accessible": (spec.get("orchestration") or {}).get("tool_not_accessible")},
            "tools": [{"type": (t.get("tool_spec") or {}).get("type"), "name": (t.get("tool_spec") or {}).get("name")}
                      for t in spec.get("tools") or []],
            "tool_resources": {k: {f: v[f] for f in keep if f in v} for k, v in res.items() if isinstance(v, dict)},
            "mcp_servers": [{"name": (m.get("server_spec") or {}).get("name")} for m in spec.get("mcp_servers") or []]}


def _build(account, host, show, desc=None, versions=None, grants=None, issues=None):
    """One SourceAgent from a SHOW AGENTS row plus whatever DESCRIBE / SHOW VERSIONS / SHOW GRANTS returned.
    `desc` is the DESCRIBE row, or None when it could not be read."""
    db, sch, name = show.get("database_name", ""), show.get("schema_name", ""), show["name"]
    spec, spec_state = None, "unreadable"
    if desc is not None:
        raw_spec = desc.get("agent_spec")
        if raw_spec:
            try:
                spec, spec_state = json.loads(raw_spec), "read"
            except ValueError:
                spec_state = "invalid"
        else:
            spec_state = "hidden" if str(show.get("is_secure")).lower() == "true" else "empty"
    tools, scopes, systems, models, mcp = [], [], [], ["unknown"], []
    criteria = {"is_secure": str(show.get("is_secure")).lower() == "true"}
    if spec is not None:
        tools = [t["tool_spec"]["name"] for t in spec.get("tools") or [] if (t.get("tool_spec") or {}).get("name")]
        mcp = [m["server_spec"]["name"] for m in spec.get("mcp_servers") or [] if (m.get("server_spec") or {}).get("name")]
        tools += [m for m in mcp if m not in tools]
        for r in (spec.get("tool_resources") or {}).values():
            for k in ("semantic_view", "search_service", "identifier"):
                if isinstance(r, dict) and r.get(k):
                    scopes.append(r[k])
        models = [(spec.get("models") or {}).get("orchestration") or "auto"]
        b = (spec.get("orchestration") or {}).get("budget") or {}
        criteria.update(budget_seconds=b.get("seconds"), budget_tokens=b.get("tokens"),
                        tool_not_accessible=(spec.get("orchestration") or {}).get("tool_not_accessible"))
        types = {(t.get("tool_spec") or {}).get("type") for t in spec.get("tools") or []}
        criteria["has_web_search"] = "web_search" in types
        criteria["has_code_execution"] = bool(types & {"code_interpreter", "code_execution"})
        criteria["has_generic_tools"] = "generic" in types
        systems = ["web_search"] if criteria["has_web_search"] else []
    invokers = [r["grantee_name"] for r in grants or [] if str(r.get("privilege")).upper() == "USAGE" and r.get("grantee_name")]
    if invokers:
        criteria["invokers"] = len(invokers)
        criteria["public_invokers"] = "PUBLIC" in invokers
    vers = [{"version": v["name"], "created": _iso(v.get("created_on")), **({"alias": v["alias"]} if v.get("alias") else {})}
            for v in versions or [] if v.get("name")]
    default = next((v["name"] for v in versions or [] if str(v.get("is_default")).lower() == "true"), None) \
        or (vers[0]["version"] if vers else "unknown")
    created = [c for c in [_iso(show.get("created_on"))] + [v["created"] for v in vers] if c]
    owner = show.get("owner") or "unknown"
    status = "SECURE_SPEC_HIDDEN" if spec_state == "hidden" and criteria["is_secure"] else "ACTIVE"
    comment = show.get("comment") or ""
    if not comment and spec and (spec.get("instructions") or {}).get("response"):
        comment = _scrub(spec["instructions"]["response"].splitlines()[0])
    projected = _project_spec(spec) if spec is not None else None
    sha = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest() if spec is not None else None
    return SourceAgent(
        platform="snowflake", native_id=f"{account}/{db}.{sch}.{name}", name=_display(show), description=comment,
        version=default, owner=owner, status=status, last_updated=max(created) if created else None,
        url=f"https://{host}/", provider=PROVIDER, tools=tools, data_scopes=list(dict.fromkeys(scopes)),
        systems=systems, value_limits={}, criteria=criteria,
        identity={"type": "caller_rights", "principal": owner, "distinct": False, "invokers": invokers},
        versions=vers, dependencies={"models": [Dep(m) for m in models], "mcp_servers": [Dep(m) for m in mcp]},
        extra={"account_host": host, "database": db, "schema": sch, "spec_state": spec_state, "spec_sha256": sha,
               "default_version": default if vers else None, "profile": _scrub(show.get("profile")),
               "gaps": list(issues or [])},
        raw=_redact({"show": show, "describe": {"agent_spec": projected} if projected is not None else None}))


# ------------------------------------------------------------------ adapter
class SnowflakeAdapter(AdapterBase):
    platform = "snowflake"

    def list_agents(self):
        return []  # Snowflake is connection-driven (discover); it is not part of the intake job

    def auth_methods(self):
        return ["workload_identity_federation",  # primary: Domino OIDC token, secretless
                "oauth_pkce",                      # fallback: Snowflake OAuth custom client
                "key_pair"]                        # fallback: key-pair JWT

    @staticmethod
    def _sample(cfg):
        return bool((cfg or {}).get("sample"))

    @staticmethod
    def _acct(cfg):
        return (cfg or {}).get("account") or "unknown"

    def test_connection(self, cfg, creds):
        if self._sample(cfg):
            from backend.connections import sample
            return sample.permissions("snowflake", recheck=True)
        items = []
        rows, err = _sql(cfg, creds, "SHOW AGENTS IN ACCOUNT")
        det = "SHOW AGENTS IN ACCOUNT"
        if err:
            items.append({"name": "Agents visible", "detail": det, "required": True, "ok": False, "fix": _fix_for(err)})
        elif not rows:
            items.append({"name": "Agents visible", "detail": det + " (0 agents)", "required": True, "ok": False,
                          "fix": GRANT_FIX})
        else:
            items.append({"name": "Agents visible", "detail": f"{det} ({len(rows)} agents)", "required": True,
                          "ok": True, "fix": None})
        role = cfg.get("role") or "ACT_READER"
        grows, gerr = _sql(cfg, creds, f"SHOW GRANTS TO ROLE {role}")
        gdet = f"SHOW GRANTS TO ROLE {role}"
        if gerr:
            items.append({"name": "Role privileges", "detail": gdet, "required": True, "ok": False, "fix": _fix_for(gerr)})
        else:
            items.append({"name": "Role privileges", "detail": gdet, "required": True, "ok": True, "fix": None})
            for w in write_grants(grows):
                items.append({"name": f"Read-only check: {w}",
                              "detail": f"ACT asked for read-only; this role can also {w}",
                              "required": False, "ok": False,
                              "fix": f"Revoke it: REVOKE {w.split(' on ')[0]} ... FROM ROLE {role}, or re-run ACT's setup SQL, then check again."})
        return items

    def list_scopes(self, cfg, creds):
        def node(i, label, kind, children, n=None):
            d = {"id": i, "label": label, "name": label, "kind": kind, "selected": True, "children": children}
            if n is not None:
                d["count"] = n
            return d
        if self._sample(cfg):
            from backend.connections import sample
            return [node(d["id"], d["label"], "database",
                         [node(c["id"], c["label"], "schema", [], c.get("agents")) for c in d.get("children", [])])
                    for d in sample.tree("snowflake")]
        rows, err = _sql(cfg, creds, "SHOW AGENTS IN ACCOUNT")
        if err:
            return []
        acct, groups = self._acct(cfg), {}
        for r in rows:
            groups.setdefault(r["database_name"], {}).setdefault(r["schema_name"], 0)
            groups[r["database_name"]][r["schema_name"]] += 1
        return [node(f"{acct}/{db}", db, "database",
                     [node(f"{acct}/{db}.{s}", f"{db}.{s}", "schema", [], n) for s, n in sorted(schemas.items())],
                     sum(schemas.values()))
                for db, schemas in sorted(groups.items())]

    # ---- discovery
    def _crawl(self, cfg, creds, scopes, progress=None):
        """-> (agents, issues[{reason, fix}], visible_rows)"""
        issues, agents = [], []
        rows, err = _sql(cfg, creds, "SHOW AGENTS IN ACCOUNT")
        if err:
            issues.append({"reason": f"ACT cannot list agents (Snowflake {err.get('code') or err.get('status')}).",
                           "fix": _fix_for(err)})
            return [], issues, []
        acct, host = self._acct(cfg), _host(cfg)
        want = {str(s) for s in scopes or []}
        if want:
            rows = [r for r in rows if f"{acct}/{r['database_name']}.{r['schema_name']}" in want
                    or f"{acct}/{r['database_name']}" in want]
        for n, row in enumerate(rows):
            agents.append(self._read_one(cfg, creds, acct, host, row, issues))
            if progress:
                progress(n + 1, len(rows), f"{row['database_name']}.{row['schema_name']}.{row['name']}")
        return agents, issues, rows

    def _read_one(self, cfg, creds, acct, host, row, issues):
        fq = f'"{row["database_name"]}"."{row["schema_name"]}"."{row["name"]}"'
        label = f"{row['database_name']}.{row['schema_name']}.{row['name']}"
        gaps = []
        d, derr = _sql(cfg, creds, f"DESCRIBE AGENT {fq}")
        desc = d[0] if d and not derr else None
        if desc is None:
            gaps.append("describe")
            issues.append({"reason": f"Could not read the spec of {label}; its tools, model and data are unknown.",
                           "fix": "Grant MONITOR on the agent to ACT_READER: GRANT MONITOR ON AGENT " + label + " TO ROLE ACT_READER;"})
        elif not desc.get("agent_spec") and str(row.get("is_secure")).lower() == "true":
            issues.append({"reason": f"{label} is a secure agent, so its tools are hidden from ACT.",
                           "fix": "Ask the agent's owner to grant OWNERSHIP-level visibility, or accept a partial record; ACT infers tools from traces."})
        v, verr = _sql(cfg, creds, f"SHOW VERSIONS IN AGENT {fq}")
        if verr:
            gaps.append("versions")
        g, gerr = _sql(cfg, creds, f"SHOW GRANTS ON AGENT {fq}")
        if gerr:
            gaps.append("grants")
        return _build(acct, host, row, desc, None if verr else v, None if gerr else g, gaps)

    def discover(self, cfg, creds, scopes, progress=None):
        if self._sample(cfg):
            from backend.connections import sample
            acct, host = "EXAMPLEBANK-PROD", "examplebank-prod.snowflakecomputing.com"
            return [_build(acct, host, a["show"], a.get("describe"), a.get("versions"), a.get("grants"))
                    for a in sample.tenant("snowflake").get("agents") or []]
        agents, issues, _ = self._crawl(cfg, creds, scopes, progress)
        self._last_issues = issues
        return agents

    # ---- coverage (PO-3.5)
    def coverage(self, cfg, creds, scopes=None):
        if self._sample(cfg):
            return [{"reason": "Agents published only in Snowflake Intelligence can't be listed: Snowflake has no command for that.",
                     "fix": "Share the agent's schema with ACT_READER (MONITOR), so it shows up under SHOW AGENTS."}]
        _, issues, rows = self._crawl(cfg, creds, scopes)
        out = list(issues)
        if not rows and not issues:
            out.append({"reason": "ACT sees 0 agents.", "fix": GRANT_FIX})
        elif not rows:
            pass
        if len(rows) >= 10000:
            out.append({"reason": "SHOW AGENTS returned its 10,000 row cap, so some agents may be missing.",
                        "fix": "Select fewer databases so ACT lists them one database at a time."})
        used, uerr = _sql(cfg, creds, "SELECT DISTINCT agent_database_name, agent_schema_name, agent_name "
                                      "FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY "
                                      "WHERE start_time > DATEADD(day,-90,CURRENT_TIMESTAMP())")
        if uerr:
            out.append({"reason": "Usage history isn't readable, so ACT can't tell whether agents are hidden from it.",
                        "fix": "Run GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE ACT_READER; as ACCOUNTADMIN."})
        else:
            seen = {(r["database_name"], r["schema_name"], r["name"]) for r in rows}
            missing = {(u.get("agent_database_name"), u.get("agent_schema_name"), u.get("agent_name")) for u in used} - seen
            if missing:
                out.append({"reason": f"{len(missing)} agents are used in the last 90 days but ACT can't see them.",
                            "fix": GRANT_FIX})
        return out
