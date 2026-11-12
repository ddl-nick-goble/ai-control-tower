"""PO-3c Databricks adapter tests (AC PO-3.1 .. PO-3.6), written from the AC and the Databricks research, not the code.

Contract the builder must meet
- adapters/databricks.py: DatabricksAdapter gains auth_methods / test_connection / list_scopes / discover / coverage
  and a module-level `_http(method, url, **kw) -> (status, headers, json)` used for ALL network calls (tests
  monkeypatch it; query params arrive in kw["params"] or in the URL). Sample mode (cfg["sample"] true) never calls it.
- `DatabricksAdapter.coverage(cfg, creds, scopes=None) -> [{"reason": str, "fix": str}]` (PO-3.5).
- Live cfg: {"sample": False, "account_id": ACCT, "workspaces": [{"id": W, "host": "https://dbc-....cloud.databricks.com"}]};
  creds: {"token": "..."} (bearer). Workspace APIs go to the workspace host; account APIs to accounts.* host.
- list_scopes: GET /api/2.0/accounts/{ACCT}/workspaces; on 403 it falls back to cfg["workspaces"]. Tree =
  account -> workspace leaves, all selected; the workspace leaf id contains the workspace id W.
- discover unions: serving endpoints (agent endpoints only), Apps (all apps; non-agent apps are "possible"),
  Knowledge Assistants, Supervisor Agents (+ /tools), UC agent services (Beta). Each source degrades alone
  (403/404/feature disabled gives nothing for that source, never raises).
- native_id: <W>:endpoint:<endpoint id> | <W>:app:<id> | <W>:ka:<id> | <W>:sa:<supervisor_agent_id> |
  <metastore_id>:agent-service:<cat.sch.name>.  extra["agent_signal"] values: "task" (task startswith agent/),
  "agent_bricks" (endpoint owned by a KA/Supervisor), "name_prefix" (agents_), "serving_endpoint_resource" (app with a
  serving_endpoint resource), "possible" (other apps). Endpoint detail: GET /api/2.0/serving-endpoints/{name}.
  Endpoint env var values that are not {{secrets/..}} references are redacted from raw/extra; extra["env_var_keys"]
  keeps the keys. UC function routine_definition is never stored verbatim.
- Recordings under fixtures/http/databricks/<name>.json = {"status","headers","json"} (real API bodies):
    account_workspaces                 200 JSON ARRAY of workspace_id/workspace_name/deployment_name/... (workspace W)
    account_workspaces_403             403 {"error_code":"PERMISSION_DENIED","message":...}
    serving_endpoints_list             200 {endpoints:[...]}: agent endpoint 1 (task agent/v1/responses), agent endpoint 2
                                       (name agents_..., no task), 1 foundation model, 1 external model, 1 KA endpoint
    serving_endpoint_get_agent         200 detail of endpoint 1: id, name, creator, tags (owner), task, state READY,
                                       last_updated_timestamp (ms), config.served_entities[entity_name,entity_version,
                                       environment_vars {{secrets/..}} ref], ai_gateway{inference_table_config,rate_limits}
    serving_endpoint_get_env_plaintext 200 detail of endpoint 2: state.config_update UPDATE_FAILED, a plain-text secret env value
    serving_endpoints_403              403 PERMISSION_DENIED on GET /api/2.0/serving-endpoints
    permissions_serving_endpoint_readonly  200 ACL: group act-readers CAN_VIEW
    permissions_serving_endpoint_write     200 ACL: act-readers CAN_MANAGE (or CAN_QUERY)
    apps_list_page1 (next_page_token) / apps_list_page2   app 1 has serving_endpoint + uc_securable resources, SP fields,
                                       user_api_scopes; app 2 has no resources
    apps_deployments                   200 {app_deployments:[...]} for app 1
    knowledge_assistants_list          200 {knowledge_assistants:[...], next_page_token}  (endpoint_name also in the endpoint list)
    knowledge_assistants_404           404 NOT_IMPLEMENTED / not available (served for KA and supervisors in no_beta mode)
    supervisor_agents_list             200 {supervisor_agents:[...]}
    supervisor_agent_tools             200 {tools:[...]} incl. a uc_function and a table tool
    agent_services_list                200 {agent_services:[{name:"agent-services/cat.sch.x", agent_service_type, comment,
                                       created_by, config{source_connection,base_path,system_prompt}}]}
    agent_services_feature_disabled    404 FEATURE_DISABLED
    uc_metastore_summary               200 {metastore_id,...}
    uc_model_versions                  200 {model_versions:[{version, created_at, model_version_dependencies{dependencies:
                                       [{function:{function_full_name}},{table:{table_full_name}}]}}]} for endpoint 1's model
    uc_functions_list                  200 {functions:[... routine_definition with an embedded token on line 2]}
    uc_connection_get                  200 connection of the agent service
    uc_403                             403 PERMISSION_DENIED on UC catalogs / effective-permissions
  Anything else asked of the router is 404 and the adapter must degrade (unknown, not invented).
- fixtures/databricks/agent-services.json (replay) is re-shaped to the real agent-services list ({agent_services:[
  {name:"agent-services/<cat>.<sch>.<n>", agent_service_type, comment, created_by, config}]}); legacy list_agents still
  yields the names liquidity-forecast-agent and model-risk-doc-agent.
- fixtures/sample_tenants/databricks.json gains "agents": 1-3 discovery-only entries (real API shape, no agent_service_id
  / properties keys).
"""
import importlib
import json
import sys
from datetime import datetime
from urllib.parse import unquote, urlparse

import pytest

import _po3c_common as C

LEGACY = {"liquidity-forecast-agent", "model-risk-doc-agent"}
ACCT = "a1b2c3d4-0000-4000-8000-0123456789ab"
WS = "1111111111111111"
HOST = "https://dbc-a1b2c3d4-e5f6.cloud.databricks.com"
CFG = {"sample": False, "account_id": ACCT, "workspaces": [{"id": WS, "host": HOST}]}
CREDS = {"token": "dapi-act-reader"}
SAMPLE_CFG = {"sample": True}


def _mod():
    return importlib.import_module("adapters.databricks")


def _adapter():
    from adapters import get_adapter
    ad = get_adapter("databricks")
    assert ad is not None
    return ad


class Router:
    def __init__(self, mode="ok", perms="readonly"):
        self.mode, self.perms, self.calls = mode, perms, []
        self.ep1 = C.rec("databricks", "serving_endpoint_get_agent")["json"]
        self.ep2 = C.rec("databricks", "serving_endpoint_get_env_plaintext")["json"]

    def __call__(self, method, url, **kw):
        self.calls.append((method, str(url), kw))
        u = urlparse(str(url))
        path = unquote(u.path).rstrip("/")
        blob = str(url) + json.dumps(kw, default=str)
        R = lambda n: C.resp(C.rec("databricks", n))  # noqa: E731
        if (u.hostname or "").startswith("accounts."):
            return R("account_workspaces_403" if self.mode == "account_denied" else "account_workspaces")
        if path == "/api/2.0/serving-endpoints":
            return R("serving_endpoints_403" if self.mode == "denied" else "serving_endpoints_list")
        if path.startswith("/api/2.0/serving-endpoints/"):
            n = path.rsplit("/", 1)[1]
            if n == self.ep1["name"]:
                return R("serving_endpoint_get_agent")
            if n == self.ep2["name"]:
                return R("serving_endpoint_get_env_plaintext")
            return C.not_found()
        if path.startswith("/api/2.0/permissions/serving-endpoints"):
            return R(f"permissions_serving_endpoint_{self.perms}")
        if path == "/api/2.0/apps":
            return R("apps_list_page2" if "page_token" in blob and
                     C.rec("databricks", "apps_list_page1")["json"]["next_page_token"] in blob else "apps_list_page1")
        if path.startswith("/api/2.0/apps/") and path.endswith("/deployments"):
            return R("apps_deployments")
        if path == "/api/2.1/knowledge-assistants":
            return R("knowledge_assistants_404" if self.mode == "no_beta" else "knowledge_assistants_list")
        if path == "/api/2.1/supervisor-agents":
            return R("knowledge_assistants_404" if self.mode == "no_beta" else "supervisor_agents_list")
        if path.startswith("/api/2.1/supervisor-agents/") and path.endswith("/tools"):
            return R("supervisor_agent_tools")
        if path == "/api/2.1/unity-catalog/agent-services":
            return R("agent_services_feature_disabled" if self.mode == "no_beta" else "agent_services_list")
        if path == "/api/2.1/unity-catalog/metastore_summary":
            return R("uc_metastore_summary")
        if path.startswith("/api/2.1/unity-catalog/models/") and path.endswith("/versions"):
            return R("uc_model_versions")
        if path == "/api/2.1/unity-catalog/functions":
            return R("uc_functions_list")
        if path.startswith("/api/2.1/unity-catalog/connections/"):
            return R("uc_connection_get")
        if path == "/api/2.1/unity-catalog/catalogs" or "effective-permissions" in path:
            if self.mode == "uc_denied":
                return R("uc_403")
            return 200, {}, {"catalogs": [{"name": "bank_prod"}], "privilege_assignments": []}
        if path == "/api/2.0/preview/scim/v2/Me":
            return 200, {}, {"userName": "act-reader", "id": "1"}
        return C.not_found()


@pytest.fixture
def live(monkeypatch):
    def make(mode="ok", perms="readonly"):
        r = Router(mode, perms)
        monkeypatch.setattr(_mod(), "_http", r, raising=True)
        return r
    return make


def _recording_text():
    d = C.FIX / "http" / "databricks"
    return "\n".join(p.read_text() for p in d.glob("*.json")) if d.exists() else ""


def _iso_ms(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000


def _disc(live, **kw):
    r = live(**kw)
    ad = _adapter()
    ids = [n["id"] for n in C.leaves(ad.list_scopes(CFG, CREDS))]
    return r, ad, ids, ad.discover(CFG, CREDS, ids)


def _by(agents, name):
    return next(a for a in agents if a.name == name)


def _kind(agents, kind):
    return [a for a in agents if f":{kind}:" in a.native_id]


# ---------------------------------------------------------------- PO-3.1
def test_po31_registry_adapter_implements_all_four_methods():
    ad = _adapter()
    from adapters.base import AdapterBase
    assert isinstance(ad, AdapterBase)
    for n in ("auth_methods", "test_connection", "list_scopes", "discover"):
        assert getattr(type(ad), n) is not getattr(AdapterBase, n), n


def test_po31_auth_methods_primary_and_fallback():
    m = _adapter().auth_methods()
    assert isinstance(m, list) and len(m) >= 2 and len(set(map(str, m))) == len(m)


def _find(items, *words):
    return [i for i in items if all(w in (i["name"] + " " + i["detail"]).lower() for w in words)]


def test_po31_test_connection_success_all_ok_no_write_flag(live):
    live()
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    for words in (("serving",), ("apps",), ("unity catalog",), ("agent services",)):
        got = _find(items, *words)
        assert got and all(i["ok"] for i in got), (words, items)
    assert _find(items, "serving")[0]["required"] is True
    assert not C.has_write_flag(items)


def test_po31_test_connection_serving_denied_gives_research_fix(live):
    live("denied")
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    bad = [i for i in _find(items, "serving") if not i["ok"]]
    assert bad, items
    assert "CAN_VIEW" in bad[0]["fix"] and "act-readers" in bad[0]["fix"]


def test_po31_test_connection_uc_denied_asks_for_browse(live):
    live("uc_denied")
    bad = [i for i in _find(_adapter().test_connection(CFG, CREDS), "unity catalog") if not i["ok"]]
    assert bad and "BROWSE" in bad[0]["fix"]


def test_po31_test_connection_beta_apis_off_is_optional_with_fix(live):
    live("no_beta")
    items = _adapter().test_connection(CFG, CREDS)
    svc = [i for i in _find(items, "agent services") if not i["ok"]]
    assert svc and svc[0]["required"] is False and "Unity Gateway" in svc[0]["fix"]
    bricks = [i for i in _find(items, "agent bricks") if not i["ok"]]
    assert bricks and bricks[0]["required"] is False and bricks[0]["fix"].strip()


def test_po31_test_connection_flags_invoke_or_write(live):
    live(perms="write")
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    flagged = C.has_write_flag(items)
    assert flagged and all(i["ok"] is False for i in flagged)


# ---------------------------------------------------------------- PO-3.2
def test_po32_scopes_sample_all_selected_no_http(monkeypatch):
    C.block_network(monkeypatch, _mod())
    C.check_tree_selected(_adapter().list_scopes(SAMPLE_CFG, {}))


def test_po32_scopes_live_account_and_workspace(live):
    r = live()
    tree = _adapter().list_scopes(CFG, CREDS)
    C.check_tree_selected(tree)
    assert any(WS in n["id"] for n in C.leaves(tree))
    assert any("accounts." in c[1] for c in r.calls)


def test_po32_scopes_fall_back_to_setup_code_workspaces_when_not_admin(live):
    live("account_denied")
    tree = _adapter().list_scopes(CFG, CREDS)
    C.check_tree_selected(tree)
    assert any(WS in n["id"] for n in C.leaves(tree))


# ---------------------------------------------------------------- PO-3.3 / 3.4 discover
def test_po34_recorded_precondition_ids_match_list(live):
    r = live()
    lst = C.rec("databricks", "serving_endpoints_list")["json"]["endpoints"]
    byname = {e["name"]: e for e in lst}
    assert r.ep1["name"] in byname and byname[r.ep1["name"]]["id"] == r.ep1["id"]
    assert r.ep2["name"] in byname and byname[r.ep2["name"]]["id"] == r.ep2["id"]


def test_po33_only_agent_endpoints_become_agents(live):
    r, _, _, agents = _disc(live)
    eps = _kind(agents, "endpoint")
    lst = C.rec("databricks", "serving_endpoints_list")["json"]["endpoints"]
    models = [e["name"] for e in lst if (e.get("task") or "").startswith("llm/")
              or "foundation_model" in json.dumps(e) or "external_model" in json.dumps(e)]
    assert models, "recording needs foundation/external model endpoints"
    names = {a.name for a in agents}
    assert not (set(models) & names)
    assert {e.name for e in eps} == {r.ep1["name"], r.ep2["name"]}


def test_po33_endpoint_field_mapping(live):
    r, _, _, agents = _disc(live)
    e, ep = _by(agents, r.ep1["name"]), r.ep1
    from adapters.base import SourceAgent
    assert isinstance(e, SourceAgent) and e.platform == "databricks"
    assert e.native_id == f"{WS}:endpoint:{ep['id']}"
    se = ep["config"]["served_entities"][0]
    assert e.version == str(se["entity_version"])
    assert e.status == "READY"
    assert abs(_iso_ms(e.last_updated) - ep["last_updated_timestamp"]) < 1000
    assert e.owner == next(t["value"] for t in ep["tags"] if t["key"] == "owner")
    assert e.url == f"{HOST}/ml/endpoints/{ep['name']}"
    assert e.provider == "Databricks"
    assert e.identity.get("type") == "system_sp_passthrough" and e.identity.get("principal") == ep["creator"]
    assert e.identity.get("distinct") is False
    assert e.extra.get("agent_signal") == "task" and e.extra.get("task") == ep["task"]
    assert e.extra.get("uc_model") == se["entity_name"]
    assert e.extra.get("workspace_id") == WS and HOST in str(e.extra.get("workspace_host"))
    igt = ep["ai_gateway"]["inference_table_config"]
    assert e.extra.get("inference_table") and igt["table_name_prefix"] in e.extra["inference_table"]
    assert "rate_limits" in e.criteria


def test_po33_endpoint_versions_tools_and_data_scopes_from_uc_model(live):
    r, _, _, agents = _disc(live)
    e = _by(agents, r.ep1["name"])
    mv = C.rec("databricks", "uc_model_versions")["json"]["model_versions"]
    assert [v["version"] for v in e.versions] == [str(v["version"]) for v in mv] or \
        sorted(v["version"] for v in e.versions) == sorted(str(v["version"]) for v in mv)
    deps = [d for v in mv for d in v["model_version_dependencies"]["dependencies"]]
    funcs = [d["function"]["function_full_name"] for d in deps if "function" in d]
    tables = [d["table"]["table_full_name"] for d in deps if "table" in d]
    assert funcs and tables
    got = C.names_of(e.tools)
    assert got and all(any(n in (f, f.split(".")[-1]) for f in funcs) for n in got)
    assert all(any(t in s for s in e.data_scopes) for t in tables), e.data_scopes


def test_po33_endpoint_statuses_and_secret_in_env_identity(live):
    r, _, _, agents = _disc(live)
    e2 = _by(agents, r.ep2["name"])
    assert e2.status == "UPDATE_FAILED"
    assert e2.extra.get("agent_signal") == "name_prefix"
    assert e2.identity.get("type") == "secret_in_env" and e2.identity.get("distinct") is False


def test_po33_env_values_and_function_bodies_redacted(live):
    r, _, _, agents = _disc(live)
    env_vars = r.ep2["config"]["served_entities"][0]["environment_vars"]
    plain = [v for v in env_vars.values() if not str(v).startswith("{{secrets/")]
    assert plain, "recording must carry a plain-text env value"
    fns = C.rec("databricks", "uc_functions_list")["json"]["functions"]
    body_lines = [ln.strip() for f in fns for ln in f["routine_definition"].splitlines()[1:] if ln.strip()]
    assert body_lines
    for a in agents:
        blob = json.dumps({"raw": a.raw, "extra": a.extra, "criteria": a.criteria}, default=str)
        for s in plain + body_lines:
            assert str(s) not in blob, f"leak in {a.name}: {s}"
    assert set(env_vars) <= set(_by(agents, r.ep2["name"]).extra.get("env_var_keys") or [])


def test_po34_apps_pagination_followed_and_mapping(live):
    r, _, _, agents = _disc(live)
    p1, p2 = (C.rec("databricks", n)["json"] for n in ("apps_list_page1", "apps_list_page2"))
    assert p1["next_page_token"] and p2["apps"]
    assert any("page_token" in c[1] + json.dumps(c[2], default=str) and p1["next_page_token"] in c[1] + json.dumps(c[2], default=str)
               for c in r.calls)
    apps = _kind(agents, "app")
    assert {a.name for a in apps} == {x["name"] for x in p1["apps"] + p2["apps"]}
    src = p1["apps"][0]
    a = _by(agents, src["name"])
    assert a.native_id == f"{WS}:app:{src['id']}" and a.url == src["url"]
    assert a.status == src["app_status"]["state"] and a.owner == src["creator"]
    assert abs(_iso_ms(a.last_updated) - _iso_ms(src["update_time"])) < 1000
    assert a.identity["type"] == "service_principal" and a.identity["principal"] == src["service_principal_name"]
    assert a.identity["distinct"] is True and a.identity.get("obo_scopes") == src["user_api_scopes"]
    assert a.extra.get("service_principal_client_id") == src["service_principal_client_id"]
    assert a.extra.get("agent_signal") == "serving_endpoint_resource"
    ser = next(x["serving_endpoint"]["name"] for x in src["resources"] if "serving_endpoint" in x)
    assert ser in C.names_of(a.tools)
    sec = next(x["uc_securable"]["securable_full_name"] for x in src["resources"] if "uc_securable" in x)
    assert any(sec in s for s in a.data_scopes)
    deps = C.rec("databricks", "apps_deployments")["json"]["app_deployments"]
    assert len(a.versions) == len(deps)
    other = _by(agents, p2["apps"][0]["name"])
    assert other.extra.get("agent_signal") == "possible"


def test_po33_knowledge_assistant_and_supervisor_mapping_one_record_per_endpoint(live):
    r, _, _, agents = _disc(live)
    ka = C.rec("databricks", "knowledge_assistants_list")["json"]["knowledge_assistants"][0]
    sa = C.rec("databricks", "supervisor_agents_list")["json"]["supervisor_agents"][0]
    a = _by(agents, ka["display_name"])
    assert a.native_id == f"{WS}:ka:{ka['id']}" and a.owner == ka["creator"] and a.version == "1"
    assert a.extra.get("endpoint_name") == ka["endpoint_name"] and a.extra.get("experiment_id") == ka["experiment_id"]
    assert a.description == ka["description"]
    s = _by(agents, sa["display_name"])
    assert s.native_id == f"{WS}:sa:{sa['supervisor_agent_id']}" and s.owner == sa["creator"]
    assert [v["version"] for v in s.versions] == ["1"]
    tools = C.rec("databricks", "supervisor_agent_tools")["json"]
    assert C.names_of(s.tools) and C.names_of(s.tools) <= set(C.strings(tools))
    tables = [x for x in C.strings(tools) if x.count(".") == 2 and x.startswith("bank_prod")]
    assert any(t in d for t in tables for d in s.data_scopes)
    for ep_name in (ka["endpoint_name"], sa["endpoint_name"]):  # the owned endpoint merges, never a duplicate
        assert len([x for x in agents if x.extra.get("endpoint_name") == ep_name or x.name == ep_name]) == 1


def test_po33_agent_service_real_fields_mapped(live):
    r, _, _, agents = _disc(live)
    svc = C.rec("databricks", "agent_services_list")["json"]["agent_services"][0]
    full = svc["name"].split("/", 1)[1]
    ms = C.rec("databricks", "uc_metastore_summary")["json"]["metastore_id"]
    a = _by(agents, full.split(".")[-1])
    assert a.native_id == f"{ms}:agent-service:{full}"
    assert a.description == svc["comment"] and a.owner == svc["created_by"]
    assert a.status == "REGISTERED" and a.version == "1" and a.provider == "External (via Databricks)"
    assert a.last_updated in (None, "unknown")
    assert a.extra.get("agent_service_type") == svc["agent_service_type"]
    assert a.extra.get("base_path") == svc["config"]["base_path"]
    assert a.identity.get("type") == "external" and svc["config"]["source_connection"]["name"].split("/")[-1] in a.identity["principal"]
    assert not a.tools and not a.data_scopes


def test_po33_unknowns_never_invented(live):
    _, _, _, agents = _disc(live)
    assert len(agents) >= 7
    for a in agents:
        C.assert_no_invented(a)
    for a in _kind(agents, "agent-service"):
        assert not a.value_limits


def test_po34_discover_serving_denied_does_not_raise_and_keeps_other_sources(live):
    live("denied")
    ad = _adapter()
    got = ad.discover(CFG, CREDS, [f"dbx:ws:{WS}"])
    assert {a.platform for a in got} == {"databricks"}
    assert _kind(got, "app"), "apps must still be returned when serving endpoints are denied"
    assert not _kind(got, "endpoint")


def test_po34_beta_apis_off_degrade_silently(live):
    live("no_beta")
    got = _adapter().discover(CFG, CREDS, [f"dbx:ws:{WS}"])
    assert _kind(got, "endpoint") and _kind(got, "app")
    assert not _kind(got, "ka") and not _kind(got, "sa") and not _kind(got, "agent-service")


# ---------------------------------------------------------------- PO-3.5
def test_po35_coverage_shape_on_success(live):
    _, ad, ids, _ = _disc(live)
    cov = ad.coverage(CFG, CREDS, ids)
    assert isinstance(cov, list)
    for c in cov:
        assert isinstance(c["reason"], str) and c["reason"] and isinstance(c["fix"], str) and c["fix"]


def test_po35_coverage_reports_beta_not_enabled(live):
    live("no_beta")
    cov = _adapter().coverage(CFG, CREDS, [f"dbx:ws:{WS}"])
    hit = [c for c in cov if "agent services" in c["reason"].lower() or "beta" in c["reason"].lower()]
    assert hit and "Unity Gateway" in hit[0]["fix"]


def test_po35_coverage_reports_endpoint_acl_gap_when_denied(live):
    live("denied")
    cov = _adapter().coverage(CFG, CREDS, [f"dbx:ws:{WS}"])
    hit = [c for c in cov if "CAN_VIEW" in c["fix"]]
    assert hit and all(c["reason"] and c["fix"] for c in cov)


# ---------------------------------------------------------------- sample mode
def test_sample_mode_no_http_all_methods(monkeypatch):
    C.block_network(monkeypatch, _mod())
    ad = _adapter()
    C.check_perm_shape(ad.test_connection(SAMPLE_CFG, {}))
    tree = ad.list_scopes(SAMPLE_CFG, {})
    C.check_tree_selected(tree)
    agents = ad.discover(SAMPLE_CFG, {}, [n["id"] for n in C.leaves(tree)])
    from adapters.base import SourceAgent
    assert agents and all(isinstance(a, SourceAgent) for a in agents)
    assert isinstance(ad.coverage(SAMPLE_CFG, {}, None), list)


def test_sample_mode_replay_plus_discovery_only_agents(monkeypatch):
    C.block_network(monkeypatch, _mod())
    extra = C.sample_cfg_agents("databricks")
    assert isinstance(extra, list) and 1 <= len(extra) <= 3, "sample_tenants/databricks.json needs 1-3 'agents'"
    for e in extra:
        assert isinstance(e, dict) and "agent_service_id" not in e and "properties" not in e
    agents = _adapter().discover(SAMPLE_CFG, {}, [])
    names = {a.name for a in agents}
    assert LEGACY <= names
    assert len(agents) == len(LEGACY) + len(extra) and len(names - LEGACY) == len(extra) >= 1
    for a in agents:
        C.assert_no_invented(a)


def test_po36_sample_permission_details_use_real_agent_bricks_paths():
    d = json.loads((C.FIX / "sample_tenants" / "databricks.json").read_text())
    blob = json.dumps(d["permissions"])
    assert "knowledge-assistants" in blob and "agent-bricks" not in blob


# ---------------------------------------------------------------- PO-3.6 fixtures and guard rails
def test_po36_replay_fixture_has_real_agent_service_shape():
    doc = json.loads((C.FIX / "databricks" / "agent-services.json").read_text())
    items = doc["agent_services"]
    assert len(items) >= 2
    for s in items:
        assert s["name"].startswith("agent-services/") and s["name"].count(".") == 2
        assert {"agent_service_type", "comment", "created_by", "config"} <= set(s)
        assert not ({"agent_service_id", "identity", "properties", "owner", "full_name", "updated_at"} & set(s))


def test_po36_legacy_list_agents_names():
    assert {a.name for a in _mod().DatabricksAdapter().list_agents()} == LEGACY


sys.path.insert(0, str(C.ROOT / "tests"))
from test_t4_intake import env, client, _intake, _fleet  # noqa: E402,F401  (reuse the AC-1 harness)


def test_po36_intake_still_yields_same_fleet(client):
    _intake()
    fleet = _fleet(client)
    assert {a["name"] for a in fleet} == {
        "branch-ops-assistant", "collections-outreach-agent", "credit-memo-drafter", "domino-deployed-agent",
        "kyc-refresh-agent", "liquidity-forecast-agent", "model-risk-doc-agent", "payments-fraud-screener",
        "regulatory-report-drafter", "relationship-manager-copilot", "trade-break-triage", "treasury-sweep-agent"}
    assert len(fleet) == 12  # 13 with the shadow row the AC-1 tests add
    assert len([a for a in fleet if a["source_platform"] == "databricks"]) == 2
