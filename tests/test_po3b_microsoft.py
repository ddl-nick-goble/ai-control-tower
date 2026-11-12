"""PO-3b Microsoft adapter tests (Foundry + Entra Agent ID + Agent 365 catalog, merged on the identity id).
Written from the AC text and the research files only.

Interface contract the builder follows (adapters/microsoft.py, class MicrosoftAdapter, get_adapter("microsoft")):
- Module-level `_http(method, url, **kw) -> (status, headers, json_body)` is the ONLY network call. kw may hold
  params=, json=, headers=. Pagination: ARM nextLink, Resource Graph options.$skipToken, Foundry has_more + after=last_id,
  Graph @odata.nextLink (call _http again with the full nextLink URL).
- cfg: {"tenant_id": "...", "client_id": "..."}; creds: {"access_token": "..."} (any per-audience token keys are fine);
  cfg["sample"] true => no HTTP.
- Foundry project endpoint is built by the adapter: https://{customSubDomainName}.services.ai.azure.com/api/projects/{project}
  (ARM project `name` is "account/project").
- coverage(cfg, creds, scopes) -> [{"reason": str, "fix": str}]   (PO-3.5; method name `coverage`)
- Merge key: Foundry instance_identity.principal_id == Graph agentIdentity.id == package.agentIdentityId.
  extra["source"] / extra text names the surfaces: "foundry", "entra_agent_id", "agent365" (substring match), and the
  package id ("P_...").
- Values the API cannot provide are the string "unknown" (owner...) or [] / ["unknown"] for lists.
- Sample mode: fixtures/sample_tenants/microsoft.json gains an "agents" list (1-3 discovery-only agents).
Recordings the builder creates under fixtures/http/microsoft/ (JSON, request + response each), at least:
  arm_subscriptions_page1.json, arm_subscriptions_page2.json, arm_arg_foundry_accounts_p1.json,
  arm_arg_foundry_accounts_p2.json, arm_projects_list.json, arm_authorizationfailed_403.json,
  foundry_agents_p1.json, foundry_agents_p2.json, foundry_agent_hosted.json, foundry_deployments.json,
  foundry_403_missing_dataaction.json, foundry_403_private_network.json, graph_agentidentities_p1.json,
  graph_agentidentities_p2.json, graph_packages_list.json, graph_packages_403_nolicence.json.
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
TENANT = "aaaa1111-0000-0000-0000-000000000001"
SUB_A = "11111111-1111-1111-1111-111111111111"
SUB_B = "22222222-2222-2222-2222-222222222222"
CFG = {"tenant_id": TENANT, "client_id": "uami-client-0000"}
CREDS = {"access_token": "eyJ.fake", "arm_token": "t1", "foundry_token": "t2", "graph_token": "t3"}
LEGACY = {"relationship-manager-copilot", "credit-memo-drafter", "branch-ops-assistant"}
ID_CM = "1b7313c4-0000-0000-0000-00000000aaaa"
ID_KYC = "1b7313c4-0000-0000-0000-00000000bbbb"
ID_TS = "1b7313c4-0000-0000-0000-00000000cccc"
SECRETS = ("sk-live-ABC123SECRET", "topsecrettoken")
ACCT = f"/subscriptions/{SUB_A}/resourceGroups/rg-ai/providers/Microsoft.CognitiveServices/accounts/bank-foundry"
ACCT_P = f"/subscriptions/{SUB_B}/resourceGroups/rg-priv/providers/Microsoft.CognitiveServices/accounts/bank-foundry-private"

AGENT_CM = {"object": "agent", "id": "credit-memo-drafter", "name": "credit-memo-drafter", "state": "enabled",
            "instance_identity": {"principal_id": ID_CM, "client_id": "cm-client", "status": "active"},
            "versions": {"latest": {
                "object": "agent.version", "name": "credit-memo-drafter", "version": "3", "status": "active",
                "description": "Drafts credit memos", "created_at": 1762300000, "metadata": {},
                "definition": {"kind": "prompt", "model": "gpt-4.1-credit", "instructions": "Be careful.", "tools": [
                    {"type": "mcp", "server_label": "core-banking", "server_url": "https://mcp.bank.internal/",
                     "project_connection_id": "cb-mcp", "allowed_tools": ["get_balance"], "require_approval": "never"},
                    {"type": "azure_ai_search", "azure_ai_search": {"indexes": [
                        {"project_connection_id": "srch", "index_name": "kyc-docs"}]}},
                    {"type": "code_interpreter"}]}}}}
AGENT_KYC = {"object": "agent", "id": "kyc-refresher", "name": "kyc-refresher", "state": "enabled",
             "instance_identity": {"principal_id": ID_KYC, "client_id": "kyc-client", "status": "active"},
             "versions": {"latest": {
                 "object": "agent.version", "name": "kyc-refresher", "version": "1", "status": "active",
                 "description": "Refreshes KYC", "created_at": 1762400000, "metadata": {},
                 "definition": {"kind": "hosted", "environment_variables": {"AZURE_OPENAI_KEY": SECRETS[0]},
                                "tools": [{"type": "mcp", "server_label": "kyc-hub", "server_url": "https://kyc.bank.internal/",
                                           "headers": {"authorization": "Bearer " + SECRETS[1]}}]}}}}

ROLE_DEFS = {
    "acdd72a7": {"roleName": "Reader", "permissions": [{"actions": ["*/read"], "dataActions": []}]},
    "2a2b9908": {"roleName": "Storage Blob Data Reader",
                 "permissions": [{"actions": [], "dataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"]}]},
    "53ca6127": {"roleName": "Foundry User", "permissions": [{"actions": [], "dataActions": ["Microsoft.CognitiveServices/*"]}]},
}


def _gerr(code, msg):
    return 403, {}, {"error": {"code": code, "message": msg}}


class Fake:
    def __init__(self, override=None, private=True, uami_role="acdd72a7"):
        self.calls, self.override, self.private, self.uami_role = [], override or {}, private, uami_role

    @staticmethod
    def params(url, kw):
        p = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        p.update({k: str(v) for k, v in (kw.get("params") or {}).items()})
        return p

    def __call__(self, method, url, **kw):
        p = self.params(url, kw)
        self.calls.append((method, url, p, kw.get("json")))
        for sub, resp in self.override.items():
            if sub in url:
                return resp
        host, path = urlparse(url).netloc, urlparse(url).path
        if host == "management.azure.com":
            return self.arm(method, url, path, p, kw)
        if host == "bank-foundry.services.ai.azure.com":
            return self.foundry(path, p)
        if host == "bank-foundry-private.services.ai.azure.com":
            return _gerr("AccessDenied", "Public access is disabled. Please configure a private endpoint.")
        if host == "graph.microsoft.com":
            return self.graph(path, p)
        return 404, {}, {"error": {"code": "NotFound"}}

    def arm(self, method, url, path, p, kw):
        if path.rstrip("/") == "/subscriptions":
            if "skiptoken=s2" in url.lower():
                return 200, {}, {"value": [{"subscriptionId": SUB_B, "displayName": "Sub B", "tenantId": TENANT,
                                            "state": "Enabled"}]}
            return 200, {}, {"value": [{"subscriptionId": SUB_A, "displayName": "Sub A", "tenantId": TENANT,
                                        "state": "Enabled"}],
                             "nextLink": "https://management.azure.com/subscriptions?api-version=2022-12-01&$skiptoken=s2"}
        if path.rstrip("/") == "/tenants":
            return 200, {}, {"value": [{"tenantId": TENANT, "displayName": "Example Bank"}]}
        if "Microsoft.ResourceGraph" in path:
            opts = (kw.get("json") or {}).get("options") or {}
            acct_a = {"id": ACCT, "name": "bank-foundry", "kind": "AIServices", "location": "eastus2",
                      "subscriptionId": SUB_A, "resourceGroup": "rg-ai", "tenantId": TENANT,
                      "properties": {"customSubDomainName": "bank-foundry", "allowProjectManagement": True,
                                     "publicNetworkAccess": "Enabled"}}
            acct_b = {"id": ACCT_P, "name": "bank-foundry-private", "kind": "AIServices", "location": "westeurope",
                      "subscriptionId": SUB_B, "resourceGroup": "rg-priv", "tenantId": TENANT,
                      "properties": {"customSubDomainName": "bank-foundry-private", "allowProjectManagement": True,
                                     "publicNetworkAccess": "Disabled"}}
            if opts.get("$skipToken") == "arg2":
                return 200, {}, {"totalRecords": 2, "count": 1, "data": [acct_b] if self.private else []}
            return 200, {}, {"totalRecords": 2, "count": 1, "data": [acct_a], "$skipToken": "arg2"}
        if path.endswith("/accounts/bank-foundry/projects"):
            return 200, {}, {"value": [{"id": ACCT + "/projects/credit-ops", "name": "bank-foundry/credit-ops",
                                        "location": "eastus2", "tags": {"Owner": "platform-team"},
                                        "systemData": {"createdBy": "dev@bank.com"},
                                        "properties": {"displayName": "Credit Ops", "isDefault": True}}]}
        if path.endswith("/accounts/bank-foundry-private/projects"):
            return 200, {}, {"value": [{"id": ACCT_P + "/projects/private-proj", "name": "bank-foundry-private/private-proj",
                                        "location": "westeurope", "properties": {"displayName": "Private"}}]}
        if path.endswith("/roleAssignments"):
            flt = p.get("$filter", "")
            if ID_CM in flt:
                return 200, {}, {"value": [{"properties": {
                    "principalId": ID_CM, "roleDefinitionId": "/providers/Microsoft.Authorization/roleDefinitions/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
                    "scope": f"/subscriptions/{SUB_A}/resourceGroups/rg-kyc/providers/Microsoft.Storage/storageAccounts/sa-kyc"}}]}
            if ID_KYC in flt or ID_TS in flt:
                return 200, {}, {"value": []}
            return 200, {}, {"value": [{"properties": {
                "principalId": "uami-principal", "scope": f"/subscriptions/{SUB_A}",
                "roleDefinitionId": f"/providers/Microsoft.Authorization/roleDefinitions/{self.uami_role}-0000-0000-0000-000000000000"}}]}
        if "/roleDefinitions/" in path:
            key = path.rsplit("/", 1)[-1][:8]
            if key in ROLE_DEFS:
                return 200, {}, {"properties": ROLE_DEFS[key]}
        return 404, {}, {"error": {"code": "ResourceNotFound"}}

    def foundry(self, path, p):
        if path.endswith("/agents"):
            if p.get("after") == "kyc-refresher" or p.get("after"):
                return 200, {}, {"data": [AGENT_KYC], "first_id": "kyc-refresher", "last_id": "kyc-refresher", "has_more": False}
            return 200, {}, {"data": [AGENT_CM], "first_id": "credit-memo-drafter", "last_id": "credit-memo-drafter",
                             "has_more": True}
        if path.endswith("/agents/credit-memo-drafter/versions"):
            return 200, {}, {"data": [AGENT_CM["versions"]["latest"],
                                      dict(AGENT_CM["versions"]["latest"], version="2", created_at=1762000000)],
                             "has_more": False}
        if path.endswith("/agents/kyc-refresher/versions"):
            return 200, {}, {"data": [AGENT_KYC["versions"]["latest"]], "has_more": False}
        if path.endswith("/agents/credit-memo-drafter"):
            return 200, {}, AGENT_CM
        if path.endswith("/agents/kyc-refresher"):
            return 200, {}, AGENT_KYC
        if path.endswith("/deployments"):
            return 200, {}, {"value": [{"name": "gpt-4.1-credit", "modelName": "gpt-4.1", "modelVersion": "2025-04-14",
                                        "modelPublisher": "OpenAI", "sku": {"name": "GlobalStandard"}}]}
        if path.endswith("/connections"):
            return 200, {}, {"value": [{"name": "srch", "type": "AzureAISearch", "target": "https://bank-search.search.windows.net",
                                        "isDefault": False, "credentials": {"type": "ApiKey"}}]}
        if path.endswith("/assistants"):
            return 200, {}, {"data": [], "has_more": False}
        return 404, {}, {"error": {"code": "NotFound"}}

    def graph(self, path, p):
        if path.endswith("/servicePrincipals/microsoft.graph.agentIdentity"):
            if p.get("$skiptoken") == "g2" or p.get("skiptoken") == "g2":
                return 200, {}, {"value": [{"id": ID_TS, "displayName": "treasury-sweeper", "accountEnabled": False,
                                            "createdDateTime": "2026-10-05T00:00:00Z", "servicePrincipalType": "ServiceIdentity",
                                            "agentIdentityBlueprintId": "bp-2", "createdByAppId": "other-app"}]}
            return 200, {}, {"value": [{"id": ID_CM, "displayName": "credit-memo-drafter", "accountEnabled": True,
                                        "createdDateTime": "2026-09-01T00:00:00Z", "servicePrincipalType": "ServiceIdentity",
                                        "agentIdentityBlueprintId": "bp-1",
                                        "createdByAppId": "0736f41a-0425-4b46-bdb5-1563eff02385"}],
                             "@odata.nextLink": "https://graph.microsoft.com/v1.0/servicePrincipals/microsoft.graph.agentIdentity?$top=100&$skiptoken=g2"}
        if path.endswith(f"/servicePrincipals/{ID_CM}/owners"):
            return 200, {}, {"value": [{"@odata.type": "#microsoft.graph.user", "id": "u1", "displayName": "Maria Lopez",
                                        "userPrincipalName": "maria.lopez@bank.com"}]}
        if path.endswith("/owners") or path.endswith("/sponsors") or path.endswith("/appRoleAssignments"):
            return 200, {}, {"value": []}
        if path.endswith("/catalog/packages"):
            if p.get("$skiptoken") == "k2" or p.get("skiptoken") == "k2":
                return 200, {}, {"value": [{"id": "P_2", "displayName": "Branch Ops Assistant", "type": "external",
                                            "shortDescription": "Branch ops", "isBlocked": True, "publisher": "Contoso",
                                            "version": "2.0.0", "agentIdentityId": None, "supportedHosts": ["Teams"],
                                            "createdDateTime": "2026-08-01T00:00:00Z",
                                            "lastModifiedDateTime": "2026-10-30T00:00:00Z"}]}
            return 200, {}, {"value": [{"id": "P_1", "displayName": "Credit Memo Drafter", "type": "custom",
                                        "shortDescription": "Drafts memos", "isBlocked": False, "publisher": "Example Bank",
                                        "version": "1.2.0", "agentIdentityId": ID_CM, "supportedHosts": ["Copilot"],
                                        "governanceMetadata": "PromptAgent", "createdDateTime": "2026-09-02T00:00:00Z",
                                        "lastModifiedDateTime": "2026-11-01T00:00:00Z"}],
                             "@odata.nextLink": "https://graph.microsoft.com/v1.0/copilot/admin/catalog/packages?$skiptoken=k2"}
        if "/catalog/packages/" in path:
            return 200, {}, {"id": path.rsplit("/", 1)[-1], "longDescription": "long", "elementDetails": []}
        return 404, {}, {"error": {"code": "NotFound"}}


def _adapter():
    from adapters import get_adapter
    ad = get_adapter("microsoft")
    assert ad is not None
    return ad


def _install(monkeypatch, fake):
    import requests
    mod = importlib.import_module("adapters.microsoft")
    monkeypatch.setattr(mod, "_http", fake)

    def boom(*a, **k):
        raise AssertionError("live adapter must go through adapters.microsoft._http")
    for n in ("get", "post", "request"):
        monkeypatch.setattr(requests, n, boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    return fake


def _flat(tree):
    for n in tree:
        yield n
        yield from _flat(n.get("children") or [])


def _discover(ad, cfg=CFG):
    ids = [n["id"] for n in _flat(ad.list_scopes(cfg, CREDS))]
    return ad.discover(cfg, CREDS, ids)


def _by_name(agents):
    out = {}
    for a in agents:
        out.setdefault(a.name, []).append(a)
    return out


def _dump(agents):
    return json.dumps([dataclasses.asdict(a) for a in agents], default=str)


# ---------------- contract ----------------

def test_contract_microsoft_adapter_implements_all_four_methods():
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
    mod = importlib.import_module("adapters.microsoft")

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
    sample = json.loads((FIX / "sample_tenants" / "microsoft.json").read_text())
    assert {i["name"] for i in items} == {p["name"] for p in sample["permissions"]}


def test_ac_po3_2_sample_scopes_all_selected(no_http):
    tree = _adapter().list_scopes({"sample": True}, {})
    nodes = list(_flat(tree))
    assert nodes and all(n["selected"] is True and "id" in n and "label" in n and "children" in n for n in nodes)
    sample = json.loads((FIX / "sample_tenants" / "microsoft.json").read_text())
    assert {n["id"] for n in nodes} >= {n["id"] for n in _flat(sample["tree"])}


def test_ac_sample_discover_adds_discovery_only_agents(no_http):
    ad = _adapter()
    sample = json.loads((FIX / "sample_tenants" / "microsoft.json").read_text())
    extra = sample.get("agents")
    assert isinstance(extra, list) and 1 <= len(extra) <= 3
    agents = ad.discover({"sample": True}, {}, [n["id"] for n in _flat(ad.list_scopes({"sample": True}, {}))])
    names = {a.name for a in agents}
    assert LEGACY <= names
    assert len(names - LEGACY) == len(extra) and all(a.platform == "microsoft" for a in agents)


def test_ac_po3_5_sample_coverage_shape(no_http):
    out = _adapter().coverage({"sample": True}, {}, [])
    assert isinstance(out, list) and all(isinstance(c, dict) and c["reason"] and c["fix"] for c in out)


# ---------------- PO-3.1 test_connection ----------------

def test_ac_po3_1_all_ok(monkeypatch):
    _install(monkeypatch, Fake(private=False))
    items = _adapter().test_connection(CFG, CREDS)
    assert len(items) >= 4 and all({"name", "detail", "required", "ok", "fix"} <= set(i) for i in items)
    assert all(i["ok"] for i in items if i["required"]), items
    text = json.dumps(items).lower()
    assert "foundry" in text and "agent 365" in text and ("entra" in text or "agent id" in text)
    assert "can also" not in text


def test_ac_po3_1_missing_permissions_exact_fixes(monkeypatch):
    _install(monkeypatch, Fake(private=False, override={
        "management.azure.com": _gerr("AuthorizationFailed", "The client does not have authorization to perform action "
                                      "'Microsoft.Resources/subscriptions/read' over scope '/subscriptions/" + SUB_A + "'"),
        "bank-foundry.services.ai.azure.com": _gerr("PermissionDenied", "The principal lacks the required data action "
                                                    "Microsoft.CognitiveServices/accounts/AIServices/agents/read"),
        "servicePrincipals/microsoft.graph.agentIdentity": _gerr("Authorization_RequestDenied",
                                                                 "Insufficient privileges to complete the operation."),
        "catalog/packages": _gerr("Forbidden", "Agent 365 licence required")}))
    items = _adapter().test_connection(CFG, CREDS)

    def bad(*keys):
        return [i for i in items if not i["ok"] and any(k in (i["name"] + i["detail"]).lower() for k in keys)]
    assert all({"name", "detail", "required", "ok", "fix"} <= set(i) for i in items)
    arm, fnd, ent, a365 = bad("azure resource", "subscription", "resource graph", "azure"), bad("foundry"), \
        bad("entra", "agent id"), bad("agent 365")
    assert arm and "reader" in " ".join(i["fix"] for i in arm).lower()
    assert fnd and "inventory reader" in " ".join(i["fix"] for i in fnd).lower()
    assert ent and "privileged role" in " ".join(i["fix"] for i in ent).lower()
    assert a365 and "licen" in " ".join(i["fix"] for i in a365).lower()
    assert all(i["required"] is False for i in a365)


def test_ac_po3_1_aadsts_code_maps_to_plain_fix(monkeypatch):
    _install(monkeypatch, Fake(private=False, override={
        "management.azure.com": (401, {}, {"error": "invalid_grant", "error_description":
                                           "AADSTS700213: No matching federated identity record found for presented assertion subject"})}))
    items = _adapter().test_connection(CFG, CREDS)
    bad = [i for i in items if not i["ok"]]
    assert bad
    fix = " ".join(i["fix"] for i in bad).lower()
    assert "service account" in fix or "re-run" in fix
    assert "aadsts" not in fix or "re-run" in fix


def test_ac_po3_1_flags_role_that_can_invoke(monkeypatch):
    _install(monkeypatch, Fake(private=False, uami_role="53ca6127"))
    items = _adapter().test_connection(CFG, CREDS)
    flagged = [i for i in items if "can also" in json.dumps(i).lower()]
    assert flagged, items
    blob = json.dumps(flagged).lower()
    assert "read-only" in blob and "foundry user" in blob


# ---------------- PO-3.2 scopes ----------------

def test_ac_po3_2_live_scopes_tenant_subscriptions_projects(monkeypatch):
    _install(monkeypatch, Fake())
    nodes = list(_flat(_adapter().list_scopes(CFG, CREDS)))
    assert nodes and all(n["selected"] is True for n in nodes)
    text = json.dumps([[n["id"], n["label"]] for n in nodes])
    assert TENANT in text
    assert SUB_A in text and SUB_B in text, "subscriptions nextLink page 2 not followed"
    assert "credit-ops" in text and "private-proj" in text, "Resource Graph $skipToken page 2 not followed"


# ---------------- PO-3.3 / 3.4 discover ----------------

def test_ac_po3_4_pagination_followed_on_every_surface(monkeypatch):
    fake = _install(monkeypatch, Fake())
    agents = _discover(_adapter())
    assert {a.name for a in agents} == {"credit-memo-drafter", "kyc-refresher", "treasury-sweeper", "Branch Ops Assistant"}
    urls = " ".join(u + json.dumps(p) for _, u, p, _ in fake.calls)
    assert "g2" in urls and "k2" in urls, "@odata.nextLink not followed"
    assert any(p.get("after") for _, u, p, _ in fake.calls if "services.ai.azure.com" in u and u.split("?")[0].endswith("/agents"))
    # the project endpoint is built by the adapter
    assert any(u.startswith("https://bank-foundry.services.ai.azure.com/api/projects/credit-ops/agents") for _, u, _, _ in fake.calls)


def test_ac_po3b_merges_duplicates_on_identity_id(monkeypatch):
    _install(monkeypatch, Fake())
    agents = _discover(_adapter())
    assert len(agents) == 4
    assert [a for a in agents if "credit" in a.name.lower() and "memo" in a.name.lower()].__len__() == 1
    cm = _by_name(agents)["credit-memo-drafter"][0]
    ex = json.dumps(cm.extra).lower()
    assert "foundry" in ex and "entra" in ex and "agent365" in ex and "p_1" in ex


def test_ac_po3_3_field_mapping_foundry_with_entra_and_package(monkeypatch):
    _install(monkeypatch, Fake())
    cm = _by_name(_discover(_adapter()))["credit-memo-drafter"][0]
    assert cm.platform == "microsoft"
    assert cm.version == "3" and len(cm.versions) == 2
    assert cm.identity["principal"] == ID_CM and cm.identity["distinct"] is True
    assert cm.identity["type"] == "entra_agent_id"
    assert "maria" in (cm.owner or "").lower()
    tools = " ".join(cm.tools)
    assert "core-banking" in tools and "get_balance" in tools and "code_interpreter" in tools
    assert any("kyc-docs" in s for s in cm.data_scopes)
    assert any("sa-kyc" in s and "storage blob data reader" in s.lower() for s in cm.data_scopes)
    assert any("mcp.bank.internal" in s for s in cm.systems)
    assert "gpt-4.1" in json.dumps(cm.dependencies)
    assert cm.provider == "OpenAI"
    assert cm.status.lower() in ("enabled/active", "enabled", "active", "enabled active") or "enabled" in cm.status.lower()
    assert (cm.last_updated or "").startswith("20")


def test_ac_po3_3_owner_fallback_and_unknowns(monkeypatch):
    _install(monkeypatch, Fake())
    by = _by_name(_discover(_adapter()))
    kyc = by["kyc-refresher"][0]
    assert kyc.owner == "platform-team"  # no Graph owners or metadata.owner: project tags.Owner
    assert kyc.identity["principal"] == ID_KYC
    assert kyc.dependencies.get("models") in (None, [], ["unknown"])
    ts = by["treasury-sweeper"][0]
    assert ts.owner == "unknown"
    assert "DISABLED" in ts.status.upper()
    assert ts.tools in ([], ["unknown"]) and ts.identity["principal"] == ID_TS
    pk = by["Branch Ops Assistant"][0]
    assert "BLOCKED" in pk.status.upper() and pk.owner == "unknown"
    assert pk.provider == "Contoso"


def test_ac_po3_3_redacts_env_vars_and_connection_keys(monkeypatch):
    _install(monkeypatch, Fake())
    agents = _discover(_adapter())
    blob = _dump(agents)
    for s in SECRETS:
        assert s not in blob


def test_ac_po3_4_agent365_not_licensed_degrades(monkeypatch):
    _install(monkeypatch, Fake(override={"catalog/packages": _gerr("Forbidden", "Agent 365 licence required")}))
    agents = _discover(_adapter())
    assert {a.name for a in agents} == {"credit-memo-drafter", "kyc-refresher", "treasury-sweeper"}


def test_ac_po3_4_foundry_permission_denied_keeps_entra_and_catalog(monkeypatch):
    _install(monkeypatch, Fake(override={"services.ai.azure.com": _gerr("PermissionDenied", "missing data action agents/read")}))
    names = {a.name for a in _discover(_adapter())}
    assert "treasury-sweeper" in names and "Branch Ops Assistant" in names
    assert not {"kyc-refresher"} & names


# ---------------- PO-3.5 coverage ----------------

def _coverage(monkeypatch, fake):
    _install(monkeypatch, fake)
    ad = _adapter()
    ids = [n["id"] for n in _flat(ad.list_scopes(CFG, CREDS))]
    out = ad.coverage(CFG, CREDS, ids)
    assert isinstance(out, list)
    assert all(isinstance(c, dict) and c["reason"] and c["fix"] for c in out)
    return out


def test_ac_po3_5_coverage_private_foundry_network(monkeypatch):
    out = _coverage(monkeypatch, Fake())
    hit = [c for c in out if "private" in c["reason"].lower() and "bank-foundry-private" in c["reason"]]
    assert hit
    assert any(k in hit[0]["fix"].lower() for k in ("peer", "private endpoint", "network"))


def test_ac_po3_5_coverage_agent365_licence(monkeypatch):
    out = _coverage(monkeypatch, Fake(override={"catalog/packages": _gerr("Forbidden", "licence")}))
    hit = [c for c in out if "agent 365" in c["reason"].lower()]
    assert hit and "licen" in hit[0]["fix"].lower()


# ---------------- PO-3.4 / 3.6 fixtures and guard rails ----------------

def test_ac_po3_4_recorded_http_files_exist():
    files = sorted((FIX / "http" / "microsoft").glob("*.json"))
    assert len(files) >= 10, files
    for f in files:
        json.loads(f.read_text())
    names = " ".join(f.name for f in files)
    for key in ("subscriptions", "agents", "agentidentities", "packages", "403"):
        assert key in names, key


def test_ac_po3_6_fixtures_reshaped_to_real_api():
    texts = {f.name: f.read_text() for f in (FIX / "microsoft").glob("*.json")}
    assert texts
    assert "customMetadata" not in "\n".join(texts.values()) and "agentName" not in "\n".join(texts.values())
    docs = [json.loads(t) for t in texts.values()]
    pk = [x for d in docs if isinstance(d, dict) for x in d.get("value", []) if "displayName" in x]
    assert pk and all(str(x["id"]).startswith("P_") for x in pk), "packages should carry real P_ ids"


def test_ac_po3_6_legacy_replay_still_gives_same_names(monkeypatch):
    for p in ("AWS", "GOOGLE", "MICROSOFT", "DATABRICKS"):
        monkeypatch.delenv(f"ACT_LIVE_{p}", raising=False)
    mod = importlib.import_module("adapters.microsoft")
    names = {a.name for a in mod.MicrosoftAdapter().list_agents()}
    assert names == LEGACY


def test_ac_po3_6_existing_intake_and_po2_tests_still_pass():
    """13 agents = the 12-agent fleet plus the shadow row asserted in test_t4_intake; names unchanged."""
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_t4_intake.py", "tests/test_po2_suggest.py",
                        "-p", "no:xdist", "-q", "-x"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, "\n".join((r.stdout + r.stderr).splitlines()[-30:])
