"""PO-3a Snowflake adapter tests (AC PO-3.1 .. PO-3.5), written from the AC and the Snowflake research.

Contract the builder must meet
- adapters/snowflake.py (new): SnowflakeAdapter, registered as "snowflake" in adapters/__init__.py REGISTRY, with
  auth_methods / test_connection / list_scopes / discover / coverage and a module-level
  `_http(method, url, **kw) -> (status, headers, json)` used for ALL network calls (SQL API:
  POST /api/v2/statements with kw["json"]={"statement": "..."}; partitions via GET /api/v2/statements/{handle}?partition=N).
- `SnowflakeAdapter.coverage(cfg, creds, scopes=None) -> [{"reason": str, "fix": str}]` (PO-3.5).
- Live cfg used here: {"sample": False, "account": "acme-prod", "host": "acme-prod.snowflakecomputing.com",
  "role": "ACT_READER", "warehouse": "ACT_WH"}; creds: {"token": "...", "token_type": "WORKLOAD_IDENTITY_FEDERATION"}.
- Recordings under fixtures/http/snowflake/ (shape {"status","headers","json"}; SQL API bodies, all values strings):
    statements_show_agents              200 SHOW AGENTS IN ACCOUNT, rowType created_on,name,database_name,schema_name,
                                        owner,comment,profile,is_secure; resultSetMetaData.partitionInfo has 2 partitions,
                                        `data` = partition 0
    statements_show_agents_partition1   200 partition 1 rows (>= 1 more agent, in a different schema)
    statements_show_agents_empty        200 zero rows (grant gap)
    describe_agent                      200 DESCRIBE AGENT of the FIRST agent of partition 0: agent_spec JSON string with
                                        models.orchestration, tools[].tool_spec, tool_resources
    statements_show_versions            200 SHOW VERSIONS IN AGENT for that agent (VERSION$N / LIVE rows)
    statements_show_grants_readonly     200 SHOW GRANTS TO ROLE ACT_READER: only USAGE/MONITOR/SELECT-like
    statements_show_grants_write        200 same, plus a write/invoke style grant (OWNERSHIP, MODIFY, OPERATE, EXECUTE ...)
    insufficient_privileges             422 {"code":"003001","sqlState":"42501","message":"... insufficient privileges ..."}
  Any other statement, or DESCRIBE/SHOW VERSIONS for the second agent, gets 422 002003 (does not exist or not
  authorized): the adapter must degrade to "unknown" for that agent, never raise, never invent.
- fixtures/sample_tenants/snowflake.json gains "agents": 1-3 discovery-only agents (real SHOW/DESCRIBE row shape).
  Snowflake is NOT in jobs/intake.py.
Unknown scalars are the string "unknown" (or None); unknown lists are [] (or ["unknown"] for models).
"""
import importlib
import json
import sys

import pytest

import _po3a_common as C
from adapters.base import AdapterBase, SourceAgent

CFG = {"sample": False, "account": "acme-prod", "host": "acme-prod.snowflakecomputing.com",
       "role": "ACT_READER", "warehouse": "ACT_WH"}
CREDS = {"token": "tok", "token_type": "WORKLOAD_IDENTITY_FEDERATION"}
SAMPLE_CFG = {"sample": True}


def _mod():
    return importlib.import_module("adapters.snowflake")


def _adapter():
    from adapters import get_adapter
    ad = get_adapter("snowflake")
    assert ad is not None, "get_adapter('snowflake') returned None"
    return ad


def rows(body):
    cols = [c["name"].lower() for c in body["resultSetMetaData"]["rowType"]]
    return [dict(zip(cols, r)) for r in body["data"]]


def _stmt(kw):
    j = kw.get("json")
    if j is None and kw.get("data"):
        j = json.loads(kw["data"]) if isinstance(kw["data"], (str, bytes)) else kw["data"]
    return ((j or {}).get("statement") or "").strip()


def _err_002003():
    return 422, {}, {"code": "002003", "sqlState": "42S02", "message": "Object does not exist or not authorized."}


class Router:
    def __init__(self, mode="ok", grants="readonly"):
        self.mode, self.grants, self.calls = mode, grants, []
        self.first = rows(C.rec("snowflake", "statements_show_agents")["json"])[0]

    def __call__(self, method, url, **kw):
        self.calls.append((method, str(url), kw))
        url = str(url)
        if method.upper() == "GET":
            if "partition=1" in url or "partition" in json.dumps(kw.get("params") or {}) and \
                    str((kw.get("params") or {}).get("partition")) == "1":
                return C.resp(C.rec("snowflake", "statements_show_agents_partition1"))
            return C.resp(C.rec("snowflake", "statements_show_agents"))
        s = _stmt(kw)
        up = s.upper()
        mine = self.first["name"].upper() in up
        if up.startswith("SHOW AGENTS"):
            if self.mode == "denied":
                return C.resp(C.rec("snowflake", "insufficient_privileges"))
            return C.resp(C.rec("snowflake", "statements_show_agents_empty" if self.mode == "empty"
                                else "statements_show_agents"))
        if self.mode == "denied":
            return C.resp(C.rec("snowflake", "insufficient_privileges"))
        if up.startswith("DESCRIBE AGENT") or up.startswith("DESCRIBE AS RESOURCE AGENT"):
            return C.resp(C.rec("snowflake", "describe_agent")) if mine else _err_002003()
        if up.startswith("SHOW VERSIONS"):
            return C.resp(C.rec("snowflake", "statements_show_versions")) if mine else _err_002003()
        if up.startswith("SHOW GRANTS") and (" TO ROLE" in up or " TO USER" in up):
            return C.resp(C.rec("snowflake", f"statements_show_grants_{self.grants}"))
        return _err_002003()


@pytest.fixture
def live(monkeypatch):
    def make(mode="ok", grants="readonly"):
        r = Router(mode, grants)
        monkeypatch.setattr(_mod(), "_http", r, raising=True)
        return r
    return make


def _all_agents_rows():
    return rows(C.rec("snowflake", "statements_show_agents")["json"]) + \
        rows(C.rec("snowflake", "statements_show_agents_partition1")["json"])


def _disp(row):
    try:
        return json.loads(row.get("profile") or "{}").get("display_name") or row["name"]
    except ValueError:
        return row["name"]


# ---------------------------------------------------------------- PO-3.1
def test_po31_registry_adapter_implements_all_four_methods():
    ad = _adapter()
    assert isinstance(ad, AdapterBase)
    for n in ("auth_methods", "test_connection", "list_scopes", "discover"):
        assert getattr(type(ad), n) is not getattr(AdapterBase, n), f"{n} not implemented"


def test_po31_auth_methods_primary_and_fallback():
    m = _adapter().auth_methods()
    assert isinstance(m, list) and len(m) >= 2 and len(set(map(str, m))) == len(m)


def test_po31_test_connection_success_marks_agent_read_ok(live):
    live()
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    ag = [i for i in items if "agent" in (i["name"] + i["detail"]).lower()]
    assert ag and all(i["ok"] for i in ag)
    assert not C.has_write_flag(items)


def test_po31_test_connection_insufficient_privileges_gives_research_fix(live):
    live("denied")
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    bad = [i for i in items if not i["ok"]]
    assert bad and any(i["required"] for i in bad)
    assert any("admin" in i["fix"].lower() for i in bad)  # "Run this as ACCOUNTADMIN ... or ask your Snowflake admin"


def test_po31_test_connection_flags_invoke_or_write(live):
    live(grants="write")
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    flagged = C.has_write_flag(items)
    assert flagged and all(i["ok"] is False for i in flagged)


# ---------------------------------------------------------------- PO-3.2
def test_po32_list_scopes_sample_all_selected(monkeypatch):
    C.block_network(monkeypatch, _mod())
    C.check_tree_selected(_adapter().list_scopes(SAMPLE_CFG, {}))


def test_po32_list_scopes_live_databases_schemas_all_selected(live):
    live()
    tree = _adapter().list_scopes(CFG, CREDS)
    C.check_tree_selected(tree)
    dbs = {r["database_name"] for r in _all_agents_rows()}
    schemas = {(r["database_name"], r["schema_name"]) for r in _all_agents_rows()}
    top = {n["label"].upper() for n in tree}
    assert {d.upper() for d in dbs} <= top | {n["id"].upper() for n in tree}
    ids_and_labels = " ".join(n["id"] + " " + n["label"] for n in C.flatten(tree)).upper()
    for d, s in schemas:
        assert s.upper() in ids_and_labels


# ---------------------------------------------------------------- PO-3.3 / PO-3.4
def _discover(live, **kw):
    r = live(**kw)
    ad = _adapter()
    tree = ad.list_scopes(CFG, CREDS)
    ids = [n["id"] for n in C.leaves(tree)]
    return r, ad, ids, ad.discover(CFG, CREDS, ids)


def test_po34_partitions_followed_all_agents_found(live):
    r, _, _, agents = _discover(live)
    meta = C.rec("snowflake", "statements_show_agents")["json"]["resultSetMetaData"]
    assert len(meta.get("partitionInfo") or []) >= 2
    want = {_disp(x) for x in _all_agents_rows()}
    assert want <= {a.name for a in agents}
    assert any(c[0].upper() == "GET" and "partition" in c[1] + json.dumps(c[2], default=str) for c in r.calls)
    assert all(isinstance(a, SourceAgent) and a.platform == "snowflake" for a in agents)


def test_po33_field_mapping_from_describe_agent(live):
    r, _, _, agents = _discover(live)
    first = r.first
    a = next(x for x in agents if x.name == _disp(first))
    assert "/" in a.native_id
    assert a.native_id.endswith(f"{first['database_name']}.{first['schema_name']}.{first['name']}")
    assert a.owner == first["owner"]
    assert a.provider == "Snowflake Cortex"
    d = rows(C.rec("snowflake", "describe_agent")["json"])[0]
    spec = json.loads(d["agent_spec"])
    want_tools = {t["tool_spec"]["name"] for t in spec["tools"]}
    assert want_tools <= C.names_of(a.tools)
    model = (spec.get("models") or {}).get("orchestration") or "auto"
    assert model in (a.dependencies.get("models") or [])
    assert a.identity.get("type") == "caller_rights" and a.identity.get("principal") == first["owner"]
    assert a.identity.get("distinct") is False
    res = spec.get("tool_resources") or {}
    ident = [v for r_ in res.values() for k, v in r_.items() if k in ("semantic_view", "search_service", "identifier")]
    assert ident and set(ident) <= set(a.data_scopes)
    assert a.versions and a.version and (a.version == "LIVE" or a.version.startswith("VERSION$"))
    assert a.status == "ACTIVE"


def test_po33_unknowns_are_unknown_not_invented(live):
    r, _, _, agents = _discover(live)
    other = [a for a in agents if a.name != _disp(r.first)]
    assert other
    for a in other:  # DESCRIBE failed (002003) for these: tools/model unknown, nothing invented
        assert not a.tools or C.names_of(a.tools) == {"unknown"}
        assert (a.dependencies.get("models") or []) in ([], ["unknown"])
        assert not a.value_limits
    for a in agents:
        C.assert_no_invented(a)


def test_po33_secrets_in_spec_redacted(live):
    r, _, _, agents = _discover(live)
    secrets = [s for s in C.strings(json.loads(C.rec("snowflake", "describe_agent")["json"]["data"][0][
        [c["name"].lower() for c in C.rec("snowflake", "describe_agent")["json"]["resultSetMetaData"]["rowType"]]
        .index("agent_spec")])) if s.lower().startswith(("sk-", "bearer ", "akia")) or "token=" in s.lower()]
    for a in agents:
        blob = json.dumps({"raw": a.raw, "extra": a.extra}, default=str)
        for s in secrets:
            assert s not in blob


def test_po34_discover_insufficient_privileges_does_not_raise(live):
    live("denied")
    assert _adapter().discover(CFG, CREDS, ["acme-prod/ANY.SCHEMA"]) == []


# ---------------------------------------------------------------- PO-3.5
def test_po35_coverage_shape_on_success(live):
    _, ad, ids, _ = _discover(live)
    cov = ad.coverage(CFG, CREDS, ids)
    assert isinstance(cov, list)
    for c in cov:
        assert c["reason"] and c["fix"]


def test_po35_coverage_flags_grant_gap_when_no_agents_visible(live):
    live("empty")
    cov = _adapter().coverage(CFG, CREDS, None)
    assert cov and all(c["reason"] and c["fix"] for c in cov)
    assert any("grant" in (c["fix"] + c["reason"]).lower() for c in cov)


# ---------------------------------------------------------------- sample mode
def test_sample_mode_no_http_all_methods(monkeypatch):
    C.block_network(monkeypatch, _mod())
    ad = _adapter()
    C.check_perm_shape(ad.test_connection(SAMPLE_CFG, {}))
    tree = ad.list_scopes(SAMPLE_CFG, {})
    C.check_tree_selected(tree)
    agents = ad.discover(SAMPLE_CFG, {}, [n["id"] for n in C.leaves(tree)])
    assert isinstance(ad.coverage(SAMPLE_CFG, {}, None), list)
    extra = C.sample_cfg_agents("snowflake")
    assert isinstance(extra, list) and 1 <= len(extra) <= 3, "sample_tenants/snowflake.json needs 1-3 'agents'"
    assert len(agents) == len(extra)  # all discovery-only: no legacy replay for Snowflake
    for a in agents:
        assert isinstance(a, SourceAgent) and a.platform == "snowflake" and a.name
        C.assert_no_invented(a)


def test_sample_mode_snowflake_not_in_intake(client):
    _intake()
    assert "snowflake" not in {a["source_platform"] for a in _fleet(client)}


sys.path.insert(0, str(C.ROOT / "tests"))
from test_t4_intake import env, client, _intake, _fleet  # noqa: E402,F401
