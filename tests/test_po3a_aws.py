"""PO-3a AWS adapter tests (AC PO-3.1 .. PO-3.6), written from the AC and the AWS research, not the implementation.

Contract the builder must meet
- adapters/aws.py: AwsAdapter gets auth_methods / test_connection / list_scopes / discover (+ coverage, below) and a
  module-level `_http(method, url, **kw) -> (status, headers, json)` used for ALL network calls.
- `AwsAdapter.coverage(cfg, creds, scopes=None) -> [{"reason": str, "fix": str}]` (PO-3.5), callable after discover.
- Live cfg used here: {"sample": False, "account_id": "123456789012", "regions": ["us-east-1"]}; creds:
  {"access_key_id","secret_access_key","session_token"}. When the account/region discovery calls fail (404 from the
  test router) list_scopes falls back to cfg and still returns a tree (account -> region), all selected.
- Recordings under fixtures/http/aws/ (shape: {"status","headers","json"}, real API bodies):
    list_agent_runtimes_page1   200 {agentRuntimes:[...], nextToken}        (POST /runtimes/ on bedrock-agentcore-control)
    list_agent_runtimes_page2   200 {agentRuntimes:[...]}                   (served when the request carries nextToken)
    get_agent_runtime           200 GetAgentRuntime body of ONE runtime from page1/page2: roleArn, status,
                                agentRuntimeVersion, environmentVariables incl. MODEL_ID and a secret-looking value
    access_denied               403 {"__type":"AccessDeniedException","message":"... not authorized to perform:
                                bedrock-agentcore:ListAgentRuntimes ..."}
    list_tags_for_resource      200 {"tags":{"owner": ...}}                 (GET /tags/{arn}, for that runtime only)
    list_gateway_targets        200 gateway targets with targetConfiguration...toolSchema (tool names)
    iam_role_policy_readonly    200 role policy for the runtime role: read-only actions + Resource ARNs (data scopes);
                                served for any host starting "iam."
    iam_role_policy_write       200 same but also grants e.g. lambda:InvokeFunction / bedrock:InvokeAgent
  Anything else the router is asked for returns 404 ResourceNotFoundException; the adapter must degrade per agent
  (unknown, not invented) and never raise.
- fixtures/sample_tenants/aws.json gains "agents": 1-3 discovery-only runtimes (real GetAgentRuntime/List shape).
"""
import importlib
import json
import sys
from urllib.parse import unquote, urlparse

import pytest

import _po3a_common as C


def _AdapterBase():
    return importlib.import_module("adapters.base").AdapterBase


def _SourceAgent():
    return importlib.import_module("adapters.base").SourceAgent


LEGACY = {"treasury-sweep-agent", "kyc-refresh-agent", "trade-break-triage"}
CFG = {"sample": False, "account_id": "123456789012", "regions": ["us-east-1"]}
CREDS = {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "s", "session_token": "t"}
SAMPLE_CFG = {"sample": True}


def _mod():
    return importlib.import_module("adapters.aws")


def _adapter():
    from adapters import get_adapter
    ad = get_adapter("aws")
    assert ad is not None, "get_adapter('aws') returned None"
    return ad


class Router:
    def __init__(self, mode="ok", iam="readonly"):
        self.mode, self.iam, self.calls = mode, iam, []
        self.rt = C.rec("aws", "get_agent_runtime")["json"]

    def __call__(self, method, url, **kw):
        self.calls.append((method, str(url), kw))
        u = urlparse(str(url))
        path, blob = unquote(u.path), str(url) + json.dumps(kw, default=str)
        if u.hostname and u.hostname.startswith("iam"):
            return C.resp(C.rec("aws", f"iam_role_policy_{self.iam}"))
        if path.rstrip("/").endswith("/runtimes") or path.rstrip("/") == "/runtimes":
            if self.mode == "denied":
                return C.resp(C.rec("aws", "access_denied"))
            return C.resp(C.rec("aws", "list_agent_runtimes_page2" if "nextToken" in blob
                                else "list_agent_runtimes_page1"))
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[0] == "runtimes":
            if parts[1] == self.rt["agentRuntimeId"]:
                return C.resp(C.rec("aws", "get_agent_runtime"))
            return C.not_found()
        if parts and parts[0] == "tags" and self.rt["agentRuntimeArn"] in path:
            return C.resp(C.rec("aws", "list_tags_for_resource"))
        if "gateways" in path and "targets" in path:
            return C.resp(C.rec("aws", "list_gateway_targets"))
        return C.not_found()


@pytest.fixture
def live(monkeypatch):
    def make(mode="ok", iam="readonly"):
        r = Router(mode, iam)
        monkeypatch.setattr(_mod(), "_http", r, raising=True)
        return r
    return make


def _all_recording_text():
    d = C.FIX / "http" / "aws"
    return "\n".join(p.read_text() for p in d.glob("*.json")) if d.exists() else ""


# ---------------------------------------------------------------- PO-3.1 contract / auth / test_connection
def test_po31_registry_adapter_implements_all_four_methods():
    ad = _adapter()
    assert isinstance(ad, _AdapterBase())
    for n in ("auth_methods", "test_connection", "list_scopes", "discover"):
        assert getattr(type(ad), n) is not getattr(_AdapterBase(), n), f"{n} not implemented"


def test_po31_auth_methods_primary_and_fallback():
    m = _adapter().auth_methods()
    assert isinstance(m, list) and len(m) >= 2 and len(set(map(str, m))) == len(m)
    assert all(isinstance(x, (str, dict)) for x in m)


def test_po31_test_connection_success_marks_runtime_read_ok(live):
    live()
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    rt = [i for i in items if "runtime" in (i["name"] + i["detail"]).lower() or "agentcore" in (i["name"] + i["detail"]).lower()]
    assert rt and all(i["ok"] for i in rt)
    assert not C.has_write_flag(items)


def test_po31_test_connection_access_denied_gives_research_fix(live):
    live("denied")
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    bad = [i for i in items if not i["ok"] and ("runtime" in (i["name"] + i["detail"]).lower() or "agentcore" in (i["name"] + i["detail"]).lower())]
    assert bad, items
    fix = bad[0]["fix"].lower()
    assert "missing" in fix and "update stack" in fix
    msg = C.rec("aws", "access_denied")["json"].get("message", "")
    for act in C.action_names(msg)[:1]:
        assert act.lower() in fix


def test_po31_test_connection_flags_invoke_or_write(live):
    live(iam="write")
    items = _adapter().test_connection(CFG, CREDS)
    C.check_perm_shape(items)
    flagged = C.has_write_flag(items)
    assert flagged and all(i["ok"] is False for i in flagged)


# ---------------------------------------------------------------- PO-3.2 scopes
def test_po32_list_scopes_sample_all_selected_no_input(monkeypatch):
    C.block_network(monkeypatch, _mod())
    C.check_tree_selected(_adapter().list_scopes(SAMPLE_CFG, {}))


def test_po32_list_scopes_live_account_region_all_selected(live):
    live()
    tree = _adapter().list_scopes(CFG, CREDS)
    C.check_tree_selected(tree)
    assert any("us-east-1" in n["id"] or "us-east-1" in n["label"] for n in C.flatten(tree))


# ---------------------------------------------------------------- PO-3.3 / PO-3.4 discover on recorded HTTP
def _discover(live, **kw):
    r = live(**kw)
    ad = _adapter()
    tree = ad.list_scopes(CFG, CREDS)
    ids = [n["id"] for n in C.leaves(tree)]
    agents = ad.discover(CFG, CREDS, ids)
    return r, ad, ids, agents


def test_po34_pagination_followed(live):
    r, _, _, agents = _discover(live)
    p1, p2 = C.rec("aws", "list_agent_runtimes_page1")["json"], C.rec("aws", "list_agent_runtimes_page2")["json"]
    assert p1.get("nextToken") and p2["agentRuntimes"]
    want = {x["agentRuntimeName"] for x in p1["agentRuntimes"] + p2["agentRuntimes"]}
    assert want <= {a.name for a in agents}
    assert any("nextToken" in c[1] + json.dumps(c[2], default=str) and "tags" not in c[1] for c in r.calls)
    assert all(isinstance(a, _SourceAgent()) and a.platform == "aws" for a in agents)


def test_po33_field_mapping_from_get_agent_runtime(live):
    r, _, _, agents = _discover(live)
    rt = r.rt
    a = next(x for x in agents if x.name == rt["agentRuntimeName"])
    assert a.native_id == rt["agentRuntimeArn"]
    assert a.version == str(rt["agentRuntimeVersion"])
    assert a.status == rt["status"]
    assert a.identity.get("type") == "iam_role" and a.identity.get("principal") == rt["roleArn"]
    model = rt["environmentVariables"]["MODEL_ID"]
    assert model in [d["name"] if isinstance(d, dict) else d for d in (a.dependencies.get("models") or [])]
    tags = C.rec("aws", "list_tags_for_resource")["json"]["tags"]
    assert a.owner == tags["owner"]
    targets = C.rec("aws", "list_gateway_targets")["json"]
    got = C.names_of(a.tools)
    assert got and got <= set(C.strings(targets)), (got, "tools must come from gateway targets")
    assert a.data_scopes and all(s in _all_recording_text() for s in a.data_scopes), a.data_scopes
    assert isinstance(a.versions, list)


def test_po33_unknowns_are_unknown_not_invented(live):
    r, _, _, agents = _discover(live)
    other = [a for a in agents if a.name != r.rt["agentRuntimeName"]]
    assert other
    for a in other:  # no tags / gateway / iam data was served for these
        assert a.owner in (None, "unknown")
        assert not a.tools or C.names_of(a.tools) == {"unknown"}
        assert not a.value_limits
    for a in agents:
        C.assert_no_invented(a)
        for ds in a.data_scopes:
            assert ds in _all_recording_text()


def test_po33_env_var_values_redacted(live):
    r, _, _, agents = _discover(live)
    env_vars = r.rt["environmentVariables"]
    secrets = [str(v) for v in env_vars.values() if len(str(v)) >= 6]
    assert secrets, "recording must carry env var values (incl. a secret-looking one)"
    for a in agents:
        blob = json.dumps({"raw": a.raw, "extra": a.extra}, default=str)
        for s in secrets:
            assert s not in blob, f"env value leaked into raw/extra of {a.name}"
    me = next(a for a in agents if a.name == r.rt["agentRuntimeName"])
    assert set(env_vars) <= set(me.extra.get("env_var_keys") or [])


def test_po34_discover_access_denied_does_not_raise(live):
    live("denied")
    ad = _adapter()
    assert ad.discover(CFG, CREDS, ["aws:acct:123456789012:us-east-1"]) == []


# ---------------------------------------------------------------- PO-3.5 coverage
def test_po35_coverage_shape_on_success(live):
    _, ad, ids, _ = _discover(live)
    cov = ad.coverage(CFG, CREDS, ids)
    assert isinstance(cov, list)
    for c in cov:
        assert isinstance(c["reason"], str) and c["reason"] and isinstance(c["fix"], str) and c["fix"]


def test_po35_coverage_reports_missing_when_denied(live):
    live("denied")
    ad = _adapter()
    cov = ad.coverage(CFG, CREDS, ["aws:acct:123456789012:us-east-1"])
    assert cov and all(c["reason"] and c["fix"] for c in cov)


# ---------------------------------------------------------------- sample mode
def test_sample_mode_no_http_all_methods(monkeypatch):
    C.block_network(monkeypatch, _mod())
    ad = _adapter()
    C.check_perm_shape(ad.test_connection(SAMPLE_CFG, {}))
    tree = ad.list_scopes(SAMPLE_CFG, {})
    C.check_tree_selected(tree)
    agents = ad.discover(SAMPLE_CFG, {}, [n["id"] for n in C.leaves(tree)])
    assert all(isinstance(a, _SourceAgent()) for a in agents)
    assert isinstance(ad.coverage(SAMPLE_CFG, {}, None), list)


def test_sample_mode_replay_plus_discovery_only_agents(monkeypatch):
    C.block_network(monkeypatch, _mod())
    extra = C.sample_cfg_agents("aws")
    assert isinstance(extra, list) and 1 <= len(extra) <= 3, "sample_tenants/aws.json needs 1-3 'agents'"
    agents = _adapter().discover(SAMPLE_CFG, {}, [])
    names = {a.name for a in agents}
    assert LEGACY <= names
    assert len(agents) == 3 + len(extra) and len(names - LEGACY) == len(extra) >= 1
    ts = next(a for a in agents if a.name == "treasury-sweep-agent")
    assert "transfer_funds" in C.names_of(ts.tools)
    assert ts.value_limits["transfer_funds"]["amount"] == 250000
    assert ts.data_scopes and all("treasury" in s for s in ts.data_scopes)
    for a in agents:
        C.assert_no_invented(a)


# ---------------------------------------------------------------- PO-3.6 guard rails
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
    assert {a["source_platform"] for a in fleet} == {"aws", "google", "microsoft", "databricks", "domino"}
    assert len([a for a in fleet if a["source_platform"] == "aws"]) == 3


def test_po36_legacy_list_agents_keeps_treasury_scenario():
    ag = {a.name: a for a in _mod().AwsAdapter().list_agents()}
    assert set(ag) == LEGACY
    ts = ag["treasury-sweep-agent"]
    assert "transfer_funds" in C.names_of(ts.tools) and ts.value_limits["transfer_funds"]["amount"] == 250000
