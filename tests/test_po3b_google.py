"""PO-3b Google adapter tests (Agent Runtime + Agent Registry + Gemini Enterprise, merged on identity).
Written from the AC text and the research files only.

Interface contract the builder follows (adapters/google.py, class GoogleAdapter, get_adapter("google")):
- Module-level `_http(method, url, **kw) -> (status, headers, json_body)` is the ONLY network call. kw may hold
  params=, json=, headers=. Pagination tokens may be passed in params or in the URL (nextPageToken / pageToken).
- cfg: {"org": "123456", "host": "bank-ai-prod", ...}; creds: {"access_token": "..."}; cfg["sample"] true => no HTTP.
- coverage(cfg, creds, scopes) -> [{"reason": str, "fix": str}]   (PO-3.5; method name `coverage`)
- discover() merges on source identity: the reasoningEngine name. extra["surfaces"] lists the surfaces that saw
  the agent, e.g. ["registry", "gemini_enterprise", "agent_runtime"].
- Values the API cannot provide are the string "unknown" (owner, status...) or [] / ["unknown"] for lists.
- Sample mode: fixtures/sample_tenants/google.json gains an "agents" list (1-3 discovery-only agents).
Recordings the builder creates under fixtures/http/google/ (JSON, request + response each), at least:
  crm_projects_search_page1.json, crm_projects_search_page2.json, crm_testIamPermissions_partial.json,
  cloudasset_searchAllResources_reasoningEngines.json, re_list_uscentral1_page1.json, re_list_uscentral1_page2.json,
  re_list_service_disabled.json, re_runtime_revisions.json, agentregistry_agents_list_page1.json,
  agentregistry_agents_list_page2.json, agentregistry_bindings_fetchAvailable.json,
  agentregistry_403_SERVICE_DISABLED.json, de_agents_list_page1.json, de_agents_list_page2.json.
"""
import dataclasses
import importlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"
CFG = {"org": "123456", "host": "bank-ai-prod", "project": "bank-ai-prod", "num": "4417"}
CREDS = {"access_token": "ya29.fake"}
LEGACY = {"payments-fraud-screener", "collections-outreach-agent", "regulatory-report-drafter"}
SECRET = "s3cr3t-hunter2"

A_NAME = "projects/bank-ai-prod/locations/us-central1/reasoningEngines/1220771026"
B_NAME = "projects/bank-ai-prod/locations/us-central1/reasoningEngines/401391792873"
A_URN = "urn:agent:projects-4417:projects:4417:locations:us-central1:aiplatform:reasoningEngines:1220771026"
SA_A = "sa-fraud@bank-ai-prod.iam.gserviceaccount.com"
DEFAULT_SA = "service-4417@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

READ_PERMS = [
    "resourcemanager.projects.get", "serviceusage.services.list", "aiplatform.reasoningEngines.list",
    "aiplatform.reasoningEngines.get", "aiplatform.reasoningEngines.getIamPolicy",
    "agentregistry.agents.list", "agentregistry.agents.get", "agentregistry.bindings.list",
    "discoveryengine.agents.list", "discoveryengine.engines.list", "cloudasset.assets.searchAllResources",
    "cloudasset.assets.searchAllIamPolicies", "logging.logEntries.list", "cloudtrace.traces.list",
]


def _err(code, status, reason=None, **meta):
    d = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason, "metadata": meta}
    return code, {}, {"error": {"code": code, "status": status, "message": f"{status} {reason}", "details": [d]}}


def _engine(name, display, spec, labels=None, **extra):
    e = {"name": name, "displayName": display, "description": f"{display} description",
         "createTime": "2026-10-02T09:00:00Z", "updateTime": "2026-11-01T08:12:09Z", "etag": "AMEw9y",
         "spec": spec}
    if labels:
        e["labels"] = labels
    e.update(extra)
    return e


ENGINE_A = _engine(A_NAME, "payments-fraud-screener", {
    "agentFramework": "google-adk", "identityType": "SERVICE_ACCOUNT", "serviceAccount": SA_A,
    "effectiveIdentity": SA_A,
    "deploymentSpec": {"env": [{"name": "MODEL", "value": "gemini-3.1-pro"}, {"name": "DB_PASSWORD", "value": SECRET}],
                       "secretEnv": [{"env": "API_KEY", "secret": "projects/4417/secrets/k"}]},
    "agentCard": {"name": "payments-fraud-screener", "skills": [{"id": "score", "name": "Score payment"}]}},
    labels={"owner": "fraud-analytics", "env": "prod"})
ENGINE_B = _engine(B_NAME, "collections-letters", {
    "agentFramework": "langgraph", "identityType": "IDENTITY_TYPE_UNSPECIFIED",
    "effectiveIdentity": DEFAULT_SA, "deploymentSpec": {}})

REG_A = {"name": "projects/bank-ai-prod/locations/us-central1/agents/agt-7f3", "agentId": A_URN,
         "displayName": "payments-fraud-screener", "description": "Scores real-time payments", "version": "1.4.0",
         "location": "us-central1", "updateTime": "2026-11-01T08:12:09Z", "createTime": "2026-10-02T09:00:00Z",
         "protocols": [{"type": "A2A_AGENT", "interfaces": [{"url": "https://fraud.example.com/a2a"}]}],
         "skills": [{"id": "hold_payment", "name": "Hold payment"}],
         "attributes": {"agentregistry.googleapis.com/system/RuntimeReference":
                        {"uri": "//aiplatform.googleapis.com/" + A_NAME}}}
REG_RUN = {"name": "projects/bank-ai-prod/locations/us-central1/agents/agt-kyc",
           "agentId": "urn:agent:projects-4417:projects:4417:locations:us-central1:run:services:kyc-helper",
           "displayName": "kyc-helper-agent", "description": "KYC helper on Cloud Run", "version": "0.9.0",
           "updateTime": "2026-10-20T00:00:00Z", "createTime": "2026-10-01T00:00:00Z",
           "skills": [{"id": "kyc", "name": "Refresh KYC"}],
           "attributes": {"agentregistry.googleapis.com/system/RuntimeIdentity":
                          {"principal": "sa-kyc@bank-ai-prod.iam.gserviceaccount.com"}}}
GE_B = {"name": "projects/4417/locations/global/collections/default_collection/engines/ge-bank/assistants/"
                "default_assistant/agents/1784401",
        "displayName": "collections-letters", "description": "Drafts hardship letters", "state": "ENABLED",
        "sharingConfig": {"scope": "RESTRICTED"}, "createTime": "2026-09-10T00:00:00Z",
        "updateTime": "2026-11-02T00:00:00Z",
        "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": B_NAME}}}
GE_HR = {"name": "projects/4417/locations/global/collections/default_collection/engines/ge-bank/assistants/"
                 "default_assistant/agents/1784402",
         "displayName": "hr-faq-workflow", "description": "HR FAQ", "state": "SUSPENDED",
         "suspensionReason": "policy", "sharingConfig": {"scope": "ALL_USERS"},
         "createTime": "2026-09-11T00:00:00Z", "updateTime": "2026-11-03T00:00:00Z"}


class Fake:
    """Routes recorded-shape Google responses. `override` maps a URL substring to a response tuple (first match)."""

    def __init__(self, override=None, granted=None):
        self.calls = []
        self.override = override or {}
        self.granted = set(READ_PERMS if granted is None else granted)

    @staticmethod
    def params(url, kw):
        p = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        p.update({k: str(v) for k, v in (kw.get("params") or {}).items()})
        return p

    def __call__(self, method, url, **kw):
        p = self.params(url, kw)
        self.calls.append((method, url, p))
        for sub, resp in self.override.items():
            if sub in url:
                return resp
        path = urlparse(url).path
        tok = p.get("pageToken")
        if "cloudresourcemanager" in url:
            if ":testIamPermissions" in url:
                asked = (kw.get("json") or {}).get("permissions", [])
                return 200, {}, {"permissions": [x for x in asked if x in self.granted]}
            if "projects:search" in url:
                if tok == "crm2":
                    return 200, {}, {"projects": [{"projectId": "trading-ml-prod", "projectNumber": "5528",
                                                   "displayName": "Trading ML", "parent": "organizations/123456",
                                                   "state": "ACTIVE"}]}
                return 200, {}, {"projects": [{"projectId": "bank-ai-prod", "projectNumber": "4417",
                                               "displayName": "Bank AI Prod", "parent": "organizations/123456",
                                               "state": "ACTIVE"}], "nextPageToken": "crm2"}
            return 404, {}, {"error": {"code": 404, "status": "NOT_FOUND"}}
        if "cloudasset" in url:
            if "searchAllIamPolicies" in url:
                if "sa-fraud" in p.get("query", ""):
                    return 200, {}, {"results": [{
                        "resource": "//bigquery.googleapis.com/projects/bank-ai-prod/datasets/payments",
                        "policy": {"bindings": [{"role": "roles/bigquery.dataViewer",
                                                 "members": ["serviceAccount:" + SA_A]}]}}]}
                return 200, {}, {"results": []}
            if tok == "cai2":
                return 200, {}, {"results": [{"name": "//aiplatform.googleapis.com/projects/trading-ml-prod/"
                                                      "locations/europe-west4/reasoningEngines/77",
                                              "assetType": "aiplatform.googleapis.com/ReasoningEngine",
                                              "project": "projects/5528", "location": "europe-west4",
                                              "displayName": "trading-signal"}]}
            return 200, {}, {"results": [
                {"name": "//aiplatform.googleapis.com/" + A_NAME, "assetType": "aiplatform.googleapis.com/ReasoningEngine",
                 "project": "projects/4417", "location": "us-central1", "displayName": "payments-fraud-screener"}],
                "nextPageToken": "cai2"}
        if "serviceusage" in url:
            return 200, {}, {"services": [{"config": {"name": n}} for n in
                                          ("aiplatform.googleapis.com", "agentregistry.googleapis.com",
                                           "discoveryengine.googleapis.com")]}
        if "aiplatform" in url:
            mine = "us-central1-aiplatform" in url and ("bank-ai-prod" in url or "/4417/" in url)
            if path.endswith("/reasoningEngines"):
                if not mine:
                    return 200, {}, {}
                if tok == "re2":
                    return 200, {}, {"reasoningEngines": [ENGINE_B]}
                return 200, {}, {"reasoningEngines": [ENGINE_A], "nextPageToken": "re2"}
            if path.endswith("/runtimeRevisions"):
                if "1220771026" in path:
                    return 200, {}, {"runtimeRevisions": [
                        {"name": A_NAME + "/runtimeRevisions/rev-2", "state": "ACTIVE", "createTime": "2026-10-20T00:00:00Z"},
                        {"name": A_NAME + "/runtimeRevisions/rev-1", "state": "ARCHIVED", "createTime": "2026-10-02T09:00:00Z"}]}
                return 200, {}, {}
            if path.endswith("/" + A_NAME.split("/")[-1]):
                return 200, {}, ENGINE_A
            if path.endswith("/" + B_NAME.split("/")[-1]):
                return 200, {}, ENGINE_B
            return 404, {}, {"error": {"code": 404, "status": "NOT_FOUND"}}
        if "agentregistry" in url:
            if path.endswith("/locations"):
                return 200, {}, {"locations": [{"locationId": "us-central1", "name": "projects/bank-ai-prod/locations/us-central1"},
                                               {"locationId": "global"}]}
            if "bindings" in path:
                src = p.get("sourceIdentifier")
                if src in (None, A_URN):
                    return 200, {}, {"bindings": [{"name": "projects/bank-ai-prod/locations/us-central1/bindings/b1",
                                                   "source": {"identifier": A_URN},
                                                   "target": {"identifier": "urn:mcp:projects-4417:payments-hub"}}]}
                return 200, {}, {"bindings": []}
            if path.endswith("/agents"):
                if "locations/us-central1" not in path:
                    return 200, {}, {}
                if tok == "ar2":
                    return 200, {}, {"agents": [REG_RUN]}
                return 200, {}, {"agents": [REG_A], "nextPageToken": "ar2"}
            return 200, {}, {}
        if "discoveryengine" in url:
            if "locations/global" not in path:
                return 200, {}, {}
            if path.endswith("/collections"):
                return 200, {}, {"collections": [{"name": "projects/4417/locations/global/collections/default_collection"}]}
            if path.endswith("/engines"):
                return 200, {}, {"engines": [{"name": "projects/4417/locations/global/collections/default_collection/engines/ge-bank",
                                              "displayName": "Bank GE"}]}
            if path.endswith("/assistants"):
                return 200, {}, {"assistants": [{"name": "projects/4417/locations/global/collections/default_collection/"
                                                         "engines/ge-bank/assistants/default_assistant"}]}
            if path.endswith("/agents"):
                if tok == "ge2":
                    return 200, {}, {"agents": [GE_HR]}
                return 200, {}, {"agents": [GE_B], "nextPageToken": "ge2"}
            return 200, {}, {}
        if "logging.googleapis.com" in url:
            return 200, {}, {}
        return 404, {}, {"error": {"code": 404, "status": "NOT_FOUND"}}


def _adapter():
    from adapters import get_adapter
    ad = get_adapter("google")
    assert ad is not None
    return ad


def _install(monkeypatch, fake):
    import requests
    mod = importlib.import_module("adapters.google")
    monkeypatch.setattr(mod, "_http", fake)

    def boom(*a, **k):
        raise AssertionError("live adapter must go through adapters.google._http")
    for n in ("get", "post", "request"):
        monkeypatch.setattr(requests, n, boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    return fake


def _flat(tree):
    for n in tree:
        yield n
        yield from _flat(n.get("children") or [])


def _discover(ad, cfg=CFG):
    tree = ad.list_scopes(cfg, CREDS)
    ids = [n["id"] for n in _flat(tree)]
    return ad.discover(cfg, CREDS, ids)


def _by_name(agents):
    out = {}
    for a in agents:
        out.setdefault(a.name, []).append(a)
    return out


def _dump(agents):
    return json.dumps([dataclasses.asdict(a) for a in agents], default=str)


# ---------------- contract ----------------

def test_contract_google_adapter_implements_all_four_methods():
    from adapters.base import AdapterBase
    ad = _adapter()
    assert isinstance(ad, AdapterBase)
    for m in ("auth_methods", "test_connection", "list_scopes", "discover"):
        assert callable(getattr(ad, m, None))
        assert getattr(type(ad), m) is not getattr(AdapterBase, m), f"{m} is still the base stub"


def test_ac_po3_1_auth_methods_primary_and_fallback():
    methods = _adapter().auth_methods()
    assert isinstance(methods, list) and len(methods) >= 2
    text = json.dumps(methods).lower()
    assert "workload" in text
    assert "oauth" in text or "sign" in text


def test_ac_po3_5_coverage_method_exists():
    assert callable(getattr(_adapter(), "coverage", None))


# ---------------- sample mode ----------------

@pytest.fixture
def no_http(monkeypatch):
    import requests
    mod = importlib.import_module("adapters.google")

    def boom(*a, **k):
        raise AssertionError("sample mode must not use HTTP")
    monkeypatch.setattr(mod, "_http", boom, raising=False)
    for n in ("get", "post", "request"):
        monkeypatch.setattr(requests, n, boom)
    monkeypatch.setattr(requests.Session, "request", boom)


def test_ac_sample_test_connection_shape(no_http):
    items = _adapter().test_connection({"sample": True}, {})
    assert items and all({"name", "detail", "required", "ok", "fix"} <= set(i) for i in items)
    assert all(i["fix"] for i in items if not i["ok"])
    sample = json.loads((FIX / "sample_tenants" / "google.json").read_text())
    assert {i["name"] for i in items} == {p["name"] for p in sample["permissions"]}


def test_ac_po3_2_sample_scopes_all_selected(no_http):
    tree = _adapter().list_scopes({"sample": True}, {})
    nodes = list(_flat(tree))
    assert nodes and all(n["selected"] is True and "id" in n and "label" in n and "children" in n for n in nodes)
    sample = json.loads((FIX / "sample_tenants" / "google.json").read_text())
    assert {n["id"] for n in nodes} >= {n["id"] for n in _flat(sample["tree"])}


def test_ac_sample_discover_adds_discovery_only_agents(no_http):
    ad = _adapter()
    sample = json.loads((FIX / "sample_tenants" / "google.json").read_text())
    extra = sample.get("agents")
    assert isinstance(extra, list) and 1 <= len(extra) <= 3
    tree = ad.list_scopes({"sample": True}, {})
    agents = ad.discover({"sample": True}, {}, [n["id"] for n in _flat(tree)])
    names = {a.name for a in agents}
    assert LEGACY <= names
    assert len(names - LEGACY) == len(extra) and all(a.platform == "google" for a in agents)


def test_ac_po3_5_sample_coverage_shape(no_http):
    out = _adapter().coverage({"sample": True}, {}, [])
    assert isinstance(out, list) and all(isinstance(c, dict) and c["reason"] and c["fix"] for c in out)


# ---------------- PO-3.1 test_connection (recorded HTTP) ----------------

def test_ac_po3_1_all_ok(monkeypatch):
    _install(monkeypatch, Fake())
    items = _adapter().test_connection(CFG, CREDS)
    assert len(items) >= 4 and all({"name", "detail", "required", "ok", "fix"} <= set(i) for i in items)
    assert all(i["ok"] for i in items if i["required"])
    text = json.dumps(items).lower()
    assert "runtime" in text and "registry" in text and "gemini" in text
    assert "can also" not in text


def test_ac_po3_1_missing_permission_has_exact_fix(monkeypatch):
    perm = "discoveryengine.agents.list"
    granted = [p for p in READ_PERMS if not p.startswith("discoveryengine.")]
    _install(monkeypatch, Fake(granted=granted, override={
        "discoveryengine.googleapis.com": _err(403, "PERMISSION_DENIED", "IAM_PERMISSION_DENIED", permission=perm)}))
    items = _adapter().test_connection(CFG, CREDS)
    bad = [i for i in items if not i["ok"] and "discoveryengine" in json.dumps(i).lower()]
    assert bad, items
    for i in bad:
        assert i["fix"]
    blob = " ".join(json.dumps(i).lower() for i in bad)
    assert perm in blob
    assert any(k in " ".join(i["fix"].lower() for i in bad) for k in ("re-run", "rerun", "role"))


def test_ac_po3_1_service_disabled_fix(monkeypatch):
    _install(monkeypatch, Fake(override={
        "agentregistry.googleapis.com": _err(403, "PERMISSION_DENIED", "SERVICE_DISABLED",
                                             service="agentregistry.googleapis.com",
                                             activationUrl="https://console.developers.google.com/apis/api/agentregistry")}))
    items = _adapter().test_connection(CFG, CREDS)
    bad = [i for i in items if not i["ok"] and "registry" in json.dumps(i).lower()]
    assert bad, items
    fix = " ".join(i["fix"] for i in bad).lower()
    assert "bank-ai-prod" in fix
    assert any(k in fix for k in ("turned on", "enable", "is off", "isn't on"))


def test_ac_po3_1_flags_role_that_can_invoke(monkeypatch):
    _install(monkeypatch, Fake(granted=READ_PERMS + ["aiplatform.reasoningEngines.query"]))
    items = _adapter().test_connection(CFG, CREDS)
    flagged = [i for i in items if "can also" in json.dumps(i).lower()]
    assert flagged, items
    blob = json.dumps(flagged).lower()
    assert "read-only" in blob and ("query" in blob or "invoke" in blob)


# ---------------- PO-3.2 scopes ----------------

def test_ac_po3_2_live_scopes_projects_locations_all_selected(monkeypatch):
    fake = _install(monkeypatch, Fake())
    tree = _adapter().list_scopes(CFG, CREDS)
    nodes = list(_flat(tree))
    assert nodes and all(n["selected"] is True for n in nodes)
    text = json.dumps([[n["id"], n["label"]] for n in nodes])
    assert "bank-ai-prod" in text and "trading-ml-prod" in text, "project search pagination (page 2) not followed"
    assert "us-central1" in text


# ---------------- PO-3.3 / PO-3.4 discover ----------------

def test_ac_po3_4_pagination_followed_on_every_surface(monkeypatch):
    fake = _install(monkeypatch, Fake())
    agents = _discover(_adapter())
    names = {a.name for a in agents}
    assert names == {"payments-fraud-screener", "collections-letters", "kyc-helper-agent", "hr-faq-workflow"}
    seen = {t for _, _, p in fake.calls for t in [p.get("pageToken")] if t}
    assert {"re2", "ar2", "ge2"} <= seen


def test_ac_po3b_merges_duplicates_on_identity(monkeypatch):
    _install(monkeypatch, Fake())
    agents = _discover(_adapter())
    assert len(agents) == 4
    by = _by_name(agents)
    assert all(len(v) == 1 for v in by.values()), "same agent must yield ONE SourceAgent"
    a = by["payments-fraud-screener"][0]
    assert "reasoningEngines/1220771026" in a.native_id
    surf = json.dumps(a.extra.get("surfaces"))
    assert "agent_runtime" in surf and "registry" in surf
    assert {"Score payment", "Hold payment"} <= set(a.tools) or {"score", "hold_payment"} <= set(a.tools)
    b = by["collections-letters"][0]
    assert "reasoningEngines/401391792873" in b.native_id
    surf = json.dumps(b.extra.get("surfaces"))
    assert "agent_runtime" in surf and "gemini_enterprise" in surf


def test_ac_po3_3_field_mapping_agent_runtime(monkeypatch):
    _install(monkeypatch, Fake())
    by = _by_name(_discover(_adapter()))
    a = by["payments-fraud-screener"][0]
    assert a.platform == "google"
    assert a.owner == "fraud-analytics"
    assert a.identity["principal"] == SA_A and a.identity["distinct"] is True
    assert "gemini-3.1-pro" in json.dumps(a.dependencies)
    assert len(a.versions) == 2
    assert any("bigquery" in s.lower() and "dataviewer" in s.lower() for s in a.data_scopes)
    assert any("bigquery" in s.lower() for s in a.systems)
    assert "payments-hub" in json.dumps(a.dependencies)
    assert a.status.upper() == "DEPLOYED"
    assert (a.last_updated or "").startswith("2026-11-01")


def test_ac_po3_3_unknown_not_invented(monkeypatch):
    _install(monkeypatch, Fake())
    by = _by_name(_discover(_adapter()))
    b = by["collections-letters"][0]
    assert b.owner == "unknown"
    assert b.identity["distinct"] is False and b.identity["principal"] == DEFAULT_SA
    assert b.tools in ([], ["unknown"])
    assert "gemini" not in json.dumps(b.dependencies.get("models", [])).lower()
    hr = by["hr-faq-workflow"][0]
    assert hr.owner == "unknown"
    assert hr.status.lower() in ("inactive", "suspended")
    assert hr.identity.get("type") == "google_managed"
    kyc = by["kyc-helper-agent"][0]
    assert kyc.identity["principal"] == "sa-kyc@bank-ai-prod.iam.gserviceaccount.com"


def test_ac_po3_3_redacts_env_values(monkeypatch):
    _install(monkeypatch, Fake())
    agents = _discover(_adapter())
    assert SECRET not in _dump(agents)


def test_ac_po3_4_missing_permission_degrades_to_runtime_only(monkeypatch):
    _install(monkeypatch, Fake(override={
        "agentregistry.googleapis.com": _err(403, "PERMISSION_DENIED", "SERVICE_DISABLED", service="agentregistry.googleapis.com"),
        "discoveryengine.googleapis.com": _err(403, "PERMISSION_DENIED", "IAM_PERMISSION_DENIED",
                                               permission="discoveryengine.agents.list")}))
    agents = _discover(_adapter())
    assert {a.name for a in agents} == {"payments-fraud-screener", "collections-letters"}
    assert "Hold payment" not in _dump(agents) and "hold_payment" not in _dump(agents)


# ---------------- PO-3.5 coverage ----------------

def test_ac_po3_5_coverage_reports_gemini_enterprise_caller_limit(monkeypatch):
    _install(monkeypatch, Fake())
    ad = _adapter()
    tree = ad.list_scopes(CFG, CREDS)
    out = ad.coverage(CFG, CREDS, [n["id"] for n in _flat(tree)])
    assert isinstance(out, list) and out
    assert all(isinstance(c, dict) and c["reason"] and c["fix"] for c in out)
    assert any("caller" in c["reason"].lower() or "created by" in c["reason"].lower() for c in out)


# ---------------- PO-3.4 / 3.6 fixtures and guard rails ----------------

def test_ac_po3_4_recorded_http_files_exist():
    files = sorted((FIX / "http" / "google").glob("*.json"))
    assert len(files) >= 10, files
    for f in files:
        json.loads(f.read_text())
    names = " ".join(f.name for f in files)
    for key in ("agents_list_page1", "agents_list_page2", "SERVICE_DISABLED", "crm_projects_search"):
        assert key in names, key


def test_ac_po3_6_fixtures_reshaped_to_real_api():
    blobs = {f.name: f.read_text() for f in (FIX / "google").glob("*.json")}
    assert blobs
    joined = "\n".join(blobs.values())
    assert "bindingsByService" not in joined and "agentEngine" not in joined
    docs = [json.loads(t) for t in blobs.values()]
    assert any(isinstance(d, dict) and any("agentId" in x for x in d.get("agents", [])) for d in docs), \
        "expected a recorded Agent Registry v1 agents list with agentId"


def test_ac_po3_6_legacy_replay_still_gives_same_names(monkeypatch):
    for p in ("AWS", "GOOGLE", "MICROSOFT", "DATABRICKS"):
        monkeypatch.delenv(f"ACT_LIVE_{p}", raising=False)
    mod = importlib.import_module("adapters.google")
    names = {a.name for a in mod.GoogleAdapter().list_agents()}
    assert names == LEGACY


def test_ac_po3_6_existing_intake_and_po2_tests_still_pass():
    """13 agents = the 12-agent fleet plus the shadow row asserted in test_t4_intake; names unchanged."""
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_t4_intake.py", "tests/test_po2_suggest.py",
                        "-p", "no:xdist", "-q", "-x"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, "\n".join((r.stdout + r.stderr).splitlines()[-30:])
