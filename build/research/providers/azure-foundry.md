# Provider research: Microsoft (Azure AI Foundry, Entra Agent ID, Agent 365, Copilot Studio)

Researched 2026-11-12. Research only, no app code. All sources in section 9 were accessed 2026-11-12.

**Bottom line.**
- **One "Microsoft" connection, one admin action.** It covers Foundry (ARM plus the project data plane), Entra Agent ID (Graph) and the Agent 365 catalog (Graph, licence-gated), each with its own capability check.
- **Primary auth is secretless.** A user-assigned managed identity (UAMI) in the bank's tenant gets a federated identity credential (FIC) pinned to the issuer, subject and audience of ACT's Domino service account. Nothing secret is stored.
- **Fallback auth is "Sign in with Microsoft"** (delegated, auth code + PKCE).
- **No typed endpoints.** ACT finds every Foundry resource and project through Resource Graph and builds the project endpoint itself.
- **The current fixtures are mostly invented** (section 3.1). The Graph path is real, but most of the fields ACT reads are not in the real response.

---

## 1. What counts as an "agent"

| Product / object | Where it lives | Agent for ACT? | Status (Nov 2026) |
|---|---|---|---|
| **Foundry agent (current model)**: kinds `prompt`, `hosted`, `workflow`, `external`, `voice` | Project data plane `https://{sub}.services.ai.azure.com/api/projects/{project}/agents`. RBAC scope `…/accounts/{a}/projects/{p}/agents/{name}` | **Yes, primary.** Versioned and immutable: every update makes a new `version`. Each agent gets its own Entra agent blueprint and instance identity. | Agents API GA (`api-version=v1`). Prompt and hosted tracing GA. Workflow, external, voice and drafts are preview (`Foundry-Features` header). |
| **Classic agents** (`asst_…`, the old Assistants-style API with threads and runs) | Same project endpoint, `/assistants`, `/threads` | **Yes, legacy.** They carry `connected_agent` tools (classic multi-agent). | Deprecated; **retire 2027-03-31**. Azure OpenAI Assistants API retired 2026-08-26. |
| **Agent Applications** (published agents) | ARM `Microsoft.CognitiveServices/accounts/projects/applications` | Enrichment: published endpoint `baseUrl`, blueprint, `authorizationPolicy` | `2026-01-15-preview`. Being replaced by `agent_endpoint` on the agent itself (Microsoft's migration guide). |
| **Foundry Control Plane fleet inventory** (Operate > Assets) | Foundry portal only | Covers Foundry agents, Azure SRE Agent, Logic Apps agent loops and registered custom agents. Classic agents are **not** listed. | **No public API, SDK or CLI found.** ACT rebuilds the inventory itself from ARM plus the data plane. |
| **Azure SRE Agent** | ARM type `microsoft.app/agents` (in Resource Graph) | Optional: name, region and tags only | GA |
| **Entra Agent ID**: `agentIdentityBlueprint`, `agentIdentityBlueprintPrincipal`, `agentIdentity`, `agentUser` | Graph `/servicePrincipals/microsoft.graph.agentIdentity` | **Identity enrichment, plus agents seen only here** (Copilot Studio and custom agents that registered an identity) | Graph **v1.0 GA** |
| **Microsoft Agent 365 catalog**: `copilotPackage` | Graph `/copilot/admin/catalog/packages` | Agents available in M365 Copilot, Teams and Outlook (declarative, custom-engine, bots), from Copilot Studio, Agent Builder, Foundry-published, 3P | Agent 365 GA 2026-05-01. Package API on v1.0 and beta, **needs an Agent 365 licence**. Old `/beta/agentRegistry` retired from 2026-06-15. |
| **Copilot Studio agents** | Power Platform inventory (`microsoft.copilotstudio/agents`) or Dataverse `bots` | Yes. Published ones also appear in the package catalog. | Inventory API GA, **delegated only**, Power Platform Administrator |

---

## 2. Inventory APIs

Token audiences, one per resource, all minted from the same identity:

| Resource | Scope string |
|---|---|
| ARM and Resource Graph | `https://management.azure.com/.default` |
| Foundry data plane | `https://ai.azure.com/.default` |
| Microsoft Graph | `https://graph.microsoft.com/.default` |
| Log Analytics (traces) | `https://api.loganalytics.io/.default` |
| Power Platform (optional) | `https://api.powerplatform.com/.default` |

### 2.1 Discovery (ARM and Resource Graph)

| Call | api-version | Notes |
|---|---|---|
| `GET https://management.azure.com/tenants` | 2022-12-01 | Delegated only. Lists the user's home and guest tenants. |
| `GET https://management.azure.com/subscriptions` | 2022-12-01 | Pages with `nextLink` |
| `POST https://management.azure.com/providers/Microsoft.ResourceGraph/resources` | 2024-04-01 | Body `{query, options:{$top:1000, $skipToken, resultFormat:"objectArray"}}`, with optional `subscriptions[]` or `managementGroups[]`. Returns `totalRecords`, `count`, `data`, `$skipToken`. |
| `GET …/subscriptions/{s}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{a}/projects` | **2025-06-01** (GA). Also 2025-10-01-preview, 2026-01-15-preview. | Returns `value[]` with `id`, `name`, `location`, `tags`, `identity{principalId,type}`, `systemData{createdBy,createdAt,…}`, `properties{displayName, description, endpoints, isDefault, provisioningState}`, `nextLink` |
| `GET …/projects/{p}/applications` | 2026-01-15-preview | Legacy published agents: `properties{agents[{agentId,agentName}], baseUrl, agentIdentityBlueprint{clientId,principalId,kind}, defaultInstanceIdentity, authorizationPolicy{type}, isEnabled, provisioningState}`, `systemData`. Pages with `nextLink`/`$skipToken`. |
| `GET …/projects/{p}/connections` | 2025-06-01 | Connection metadata: category, target, authType. **Never** call `…/listsecrets`. |
| `GET {scope}/providers/Microsoft.Authorization/roleAssignments?$filter=assignedTo('{principalId}')` | 2022-04-01 | What each agent identity can reach (data scopes) |

Example Resource Graph response row:
```json
{"id":"/subscriptions/1111…/resourceGroups/rg-ai/providers/Microsoft.CognitiveServices/accounts/bank-foundry",
 "name":"bank-foundry","kind":"AIServices","location":"eastus2","tenantId":"aaaa…",
 "properties":{"customSubDomainName":"bank-foundry","allowProjectManagement":true,
   "publicNetworkAccess":"Disabled","endpoints":{"AI Foundry API":"https://bank-foundry.services.ai.azure.com/"}}}
```

### 2.2 Foundry project data plane

- **Base URL:** `https://{customSubDomainName}.services.ai.azure.com/api/projects/{projectName}`.
- **API version:** `api-version=v1` on every Foundry-native path. Preview features are opted into with a header, such as `Foundry-Features: DraftAgents=V1Preview`, not a different version string.
- **Version-string caveat:** ServiceNow's shipped connector uses `2025-11-15-preview` for `/agents` and `v1` for `/assistants`. ACT should try `v1` and fall back to `2025-11-15-preview` if it gets a 400 "unsupported api-version".

| Call | Returns |
|---|---|
| `GET /agents?limit=100&order=desc&after={last_id}[&kind=prompt\|hosted\|workflow\|external\|voice]` | `{data:[agent], first_id, last_id, has_more}` |
| `GET /agents/{name}` | `object:"agent"`, `id`, `name`, `state` (`enabled`/`disabled`), `configuration_state`, `versions.latest` (AgentVersionObject), `agent_endpoint`, `version_selector`, `protocol_configuration`, `authorization_schemes[]` (`Entra`, `BotService…`), `publish_approval_status`, `digital_worker_type` (`m365`), `instance_identity{principal_id, client_id, status}`, `blueprint{…}`, `blueprint_reference`, `agent_card{version, description, skills[]}` |
| `GET /agents/{name}/versions` (add the `DraftAgents` header to include drafts) | Paged list of AgentVersionObject |
| `GET /agents/{name}/versions/{v}` | `object:"agent.version"`, `id`, `name`, `version`, `description`, `metadata` (16 key-value pairs), `created_at` (epoch), `status` (`creating\|active\|failed\|deleting\|deleted`), `draft`, `definition{kind,…}`, `instance_identity`, `blueprint`, `agent_guid` |
| `GET /deployments` | Model deployments: `name`, `modelName`, `modelVersion`, `modelPublisher`, `sku` |
| `GET /connections` | `name`, `type` (`AzureAISearch`, `AppInsights`, `CognitiveSearch`, `RemoteTool`…), `target`, `isDefault`, `credentials.type` only. **Never** call `POST /connections/{n}/getConnectionWithCredentials`. |
| `GET /assistants?limit=100&after=` (classic) | `{data:[{id:"asst_…", object:"assistant", created_at, name, description, model, instructions, tools[], tool_resources, metadata}], has_more, last_id}` |
| `GET /threads`, `GET /threads/{id}/runs` (classic); `GET /openai/conversations` (current model) | Runs and conversations. **These contain customer content.** ACT should use traces instead (section 2.4). |

A prompt agent's `definition` (illustrative; field names are from the API reference):
```json
{"kind":"prompt","model":"gpt-4.1-credit","instructions":"…","rai_config":{"rai_policy_name":"bank-strict"},
 "tools":[
  {"type":"mcp","server_label":"core-banking","server_url":"https://mcp.bank.internal/","project_connection_id":"cb-mcp",
   "allowed_tools":["get_balance"],"require_approval":"never"},
  {"type":"openapi","openapi":{"name":"payments","spec":{…},"auth":{"type":"managed_identity","security_scheme":{"audience":"api://pay"}}}},
  {"type":"azure_ai_search","azure_ai_search":{"indexes":[{"project_connection_id":"srch","index_name":"kyc-docs"}]}},
  {"type":"bing_grounding","bing_grounding":{"search_configurations":[{"project_connection_id":"bing"}]}},
  {"type":"a2a","base_url":"https://agent.x/","a2a_version":"1.0","project_connection_id":"a2a-x"},
  {"type":"function","name":"draft_memo","parameters":{…}},
  {"type":"code_interpreter"},{"type":"file_search","vector_store_ids":["vs_1"]}]}
```

How to read each piece:

- **Tool types.** Read `tools[].type`. The full set is:
  - Common: `function`, `mcp`, `openapi`, `azure_ai_search`, `bing_grounding`, `azure_function`, `code_interpreter`, `file_search`, `web_search`, `toolbox`.
  - With a `_preview` suffix: `bing_custom_search_preview`, `sharepoint_grounding_preview`, `fabric_dataagent_preview`, `fabric_iq_preview`, `work_iq_preview`, `web_iq_preview`, `memory_search_preview`, `computer_use_preview`, `browser_automation_preview` (`browser_automation` also exists without the suffix).
  - Agent-to-agent: `a2a` and `a2a_preview` in the current model. Classic agents use `connected_agent{id,name,description}` instead.
- **Model.** Read `definition.model` (a deployment name), then join it to `/deployments` to get the model name and publisher.
- **Identity.** Read `instance_identity.principal_id`, which equals the Graph `agentIdentity.id`. A per-agent identity means a distinct identity. Legacy agents share the project identity, so they are **not** distinct. Classic agents run as the project managed identity.
- **Data connections.** Resolve each `project_connection_id` through `/connections` to its `target` and type. Then read Azure RBAC role assignments for the agent identity (section 2.1).
- **Versions.** `/versions` gives `version` and `created_at`.
- **Status.** Combine `state` with the latest version's `status`.
- **Owner and creator.** The data plane has **no creator field**. Use, in order: Graph owners and sponsors of the agent identity, then `metadata.owner`, then the project's `tags.Owner`, then the project's `systemData.createdBy`.

### 2.3 Microsoft Graph

| Call | Permission (app / delegated) | Notes |
|---|---|---|
| `GET /v1.0/servicePrincipals/microsoft.graph.agentIdentity?$top=100` | `AgentIdentity.Read.All`. Delegated callers also need the **Agent ID Administrator** directory role (or ownership). | `id`, `displayName`, `agentIdentityBlueprintId`, `createdByAppId`, `createdDateTime`, `accountEnabled`, `disabledByMicrosoftStatus`, `servicePrincipalType:"ServiceIdentity"`, `tags`. Add `$select=managerApplications,customSecurityAttributes` for those. Pages with `@odata.nextLink`; max page size 100. |
| `GET /v1.0/servicePrincipals/{id}/owners`, `…/sponsors` (verify the exact sponsors path) | same | Accountable humans: these feed `owner` |
| `GET /v1.0/servicePrincipals/{id}/appRoleAssignments`, `…/oauth2PermissionGrants` | `Application.Read.All` may be needed (verify) | Graph and API permissions held by the agent identity: these feed `data_scopes` |
| Blueprints: the `agentIdentityBlueprint` list (path not captured; verify on Learn) | `AgentIdentityBlueprint.Read.All` | Optional |
| `GET /v1.0/copilot/admin/catalog/packages` (also `/beta`) | `CopilotPackages.Read.All`. **Needs an Agent 365 licence.** Delegated callers need the AI Administrator role (third-party report). | `id` (`P_…`), `displayName`, `type` (`microsoft\|external\|shared\|custom`), `shortDescription`, `isBlocked`, `supportedHosts`, `elementTypes`, `platform`, `publisher`, `version`, `manifestId`, `appId`, **`agentIdentityId`**, `governanceMetadata` (`PromptAgent\|HostedAgent\|WorkflowAgent\|ManagedAgent\|Unmanaged\|AIApp`), `availableTo`, `deployedTo`, `createdDateTime`, `lastModifiedDateTime`. `$filter` on `supportedHosts`, `elementTypes`, `platform`, `lastModifiedDateTime ge`. Opaque `@odata.nextLink`. |
| `GET /v1.0/copilot/admin/catalog/packages/{id}` | same | Adds `longDescription`, `categories`, `sensitivity`, `allowedUsersAndGroups`, `acquireUsersAndGroups`, and `elementDetails[{elementType, elements[{id, definition}]}]`. `definition` is a JSON **string** (bot or declarative-agent manifest). |

Example `agentIdentity` record:
```json
{"id":"1b7313c4-…","displayName":"credit-memo-drafter","createdByAppId":"0736f41a-0425-4b46-bdb5-1563eff02385",
 "agentIdentityBlueprintId":"0000…","accountEnabled":true,"servicePrincipalType":"ServiceIdentity","tags":[]}
```
`createdByAppId` = `0736f41a-…` is the Microsoft first-party app Foundry uses to create agent identities, so ACT can use it to tag an identity as Foundry-made.

### 2.4 Runs and traces

- **Where traces live.** Foundry tracing writes OTel GenAI spans (`invoke_agent`, `execute_tool`, `plan`, `invoke_workflow`) to the Application Insights resource connected to each project. Find it through the project connection of type `AppInsights` (its `target` is the resource ID).
- **Query.** `POST https://api.loganalytics.io/v1{appInsightsResourceId}/query` (resource-centric) with KQL over `AppDependencies` (workspace-based):
  ```kql
  AppDependencies | where TimeGenerated > ago(30d) and Name startswith "invoke_agent"
  | extend agent=tostring(Properties["gen_ai.agent.name"]), agentId=tostring(Properties["gen_ai.agent.id"])
  | summarize runs=count(), failures=countif(Success==false), last=max(TimeGenerated) by agent, agentId
  ```
- **Role.** Log Analytics Reader on the workspace, or Reader on the component.
- **Caveats.** The `gen_ai.*` attribute names follow conventions that are still in Development status, so verify them against real data. ACT should **only aggregate**: prompts and tool arguments can be in the spans.
- **Avoid content APIs for runs.** Classic `/threads/{id}/runs` and current `/openai/conversations` are content APIs.

### 2.5 MCP servers

- **Foundry MCP Server** (`https://mcp.ai.azure.com`, public preview, about 38 tools) and the **Azure MCP Server** both have a "list agents" tool.
- Both run with the caller's delegated token, take an endpoint or resource group, and add no inventory that the REST calls lack.
- **Recommendation:** do not use them in the ACT backend. They are only useful for a future ACT chat assistant.

### 2.6 Throttling and pagination

| API | Throttling |
|---|---|
| ARM | Token bucket per principal: about 250-request bucket, refilled at 25/s per subscription for reads. Returns 429 with `Retry-After`. |
| Resource Graph | 15 queries per 5 s per principal by default. Read headers `x-ms-user-quota-remaining` and `x-ms-user-quota-resets-after`. |
| Graph | 429 with `Retry-After` |
| Foundry data plane | Limits undocumented. Honour 429 `Retry-After` and keep to 4 or fewer concurrent calls per project. |

Pagination: `nextLink` for ARM, `$skipToken` for Resource Graph, `has_more` plus `after=last_id` for Foundry, and opaque `@odata.nextLink` for Graph.

### 2.7 Clear-text secrets to redact before storing `raw`

| Where | What to do |
|---|---|
| Hosted-agent `definition.environment_variables` | Redact |
| MCP tool `headers` and `authorization` | Redact |
| OpenAPI `spec` | May embed example keys: redact |
| `instructions` and `metadata` | Run the secret scanner |
| Connection `target` URLs | Strip SAS query strings |
| Connection secrets, account keys | Never call `getConnectionWithCredentials`, `connections/listsecrets` or `accounts/listKeys`. Reader cannot call listKeys anyway. |
| Conversation, thread and trace content | Never ingest (PII) |

---

## 3. Field mapping to `SourceAgent`

| SourceAgent | Foundry (current model) | Enrichment |
|---|---|---|
| platform | `"microsoft"` (sub-source in `extra.source`: `foundry`, `foundry_classic`, `entra_agent_id`, `agent365`) | |
| native_id | `{projectArmId}/agents/{name}` (the agent RBAC scope, stable). Classic: `{projectArmId}/assistants/{asst_id}` | Agent ID only: `entra:{agentIdentity.id}`. Package: `m365:{package.id}` |
| name / description | `name` / `versions.latest.description` | package `displayName` / `shortDescription` |
| version | `versions.latest.version` | package `version` |
| owner | Graph agent-identity owners, then sponsors, then `metadata.owner`, then project `tags.Owner`, then project `systemData.createdBy` | |
| status | `state` + latest `status` (for example `enabled/active`) | package `isBlocked` gives BLOCKED; SP `accountEnabled=false` gives DISABLED |
| last_updated | latest version `created_at` (epoch to ISO) | package `lastModifiedDateTime` |
| url | `agent_endpoint` URL if published, else `{projectEndpoint}/agents/{name}` | |
| provider | `deployments[model].modelPublisher` (OpenAI, Anthropic, Meta…) | |
| tools | `tools[]`: function `name`, mcp `server_label:allowed_tools`, openapi `name` plus operationIds, `type` for built-ins | |
| data_scopes | AI Search `index_name`@connection target, `vector_store_ids`, SharePoint/Fabric connections, memory stores | agent-identity RBAC assignments (role@scope), Graph app roles |
| systems | hosts of mcp `server_url`, openapi `servers[]`, a2a `base_url`, connection `target`s | |
| value_limits | none at source; `{}` unless `metadata` carries them | |
| criteria | `require_approval`, risky tool types (code_interpreter, computer_use, browser_automation), `authorization_schemes`, `publish_approval_status`, `digital_worker_type`, `rai_config`, account `publicNetworkAccess` | package `deployedTo`/`availableTo` |
| identity | `{type:"entra_agent_id", principal: instance_identity.principal_id, client_id, blueprint: blueprint.client_id, distinct: true}`. Legacy shared project identity or classic project MI: `distinct:false` | Graph `agentIdentityBlueprintId`, `createdByAppId` |
| versions | `/versions` mapped to `[{version, created}]` | |
| dependencies | `models:[deployment→modelName@version]`, `mcp_servers:[server_url…]`, `agents:[a2a/connected_agent targets]` | |
| extra | kind, subscription, RG, account, project, region, protocols, `agent_card.skills`, package id, `governanceMetadata` | |

### 3.1 Are today's fixtures and adapter real?

- **What is real.** The path `/beta/copilot/admin/catalog/packages` is real and also exists on v1.0. `id`, `displayName`, `version`, `publisher`, `shortDescription`, `isBlocked`, `supportedHosts`, `createdDateTime` and `lastModifiedDateTime` are real.
- **What is invented** (none of these exist in the real API):
  - `agentName`.
  - `customMetadata`, which holds owner, tools, data scopes, systems, criteria and value limits.
  - The `identity` object.
  - `owner.userPrincipalName`.
- **What is wrong.** `type:"agent"`: the real enum is `microsoft|external|shared|custom`. The fixture's `id` is a bare GUID, but real ids look like `P_…`.
- **What is missing:** `elementTypes`, `platform`, `appId`, `agentIdentityId`, `governanceMetadata`, `availableTo`, `deployedTo`, `manifestId`.
- **Consequence.** The real API does **not** carry tools, scopes or owners. ACT has to get them from `elementDetails[].definition` (the detail GET), from Foundry, and from Graph owners and sponsors.
- **Licence gate.** The API needs an Agent 365 licence and returns 403 without one.

---

## 4. Auth options, ranked by friction (lowest first)

All options use one **Microsoft** connection.

| # | Option | User steps | ACT stores | Verdict |
|---|---|---|---|---|
| 1 | **Sign in with Microsoft** (delegated, auth code + PKCE, a Domino-published multi-tenant app) | Click, sign in, consent (a Global/Privileged Role Admin consents once for the tenant) | Encrypted refresh token (90-day sliding) | **Fallback** |
| 2 | Device code (same app, no redirect URI) | Click, open microsoft.com/devicelogin, paste the code | Encrypted refresh token | Many banks block it with Conditional Access (phishing vector). Last-ditch only. |
| 3 | **Workload identity federation: UAMI + FIC pinned to Domino** | Click, run the provisioning (below), click "Return to ACT" | Tenant id, UAMI client id, nonce. **No secret.** | **Primary** |
| 4 | Client secret or certificate on a customer app registration | Admin creates the app and pastes the secret | Encrypted secret | Avoid. Offer only on request. |

### 4.1 Primary: secretless WIF (UAMI in the bank's tenant)

- **Why it is primary.**
  - App-only, so it is not tied to a person, Conditional Access sign-in-frequency rules do not break scheduled sync, and owners can leave.
  - The FIC is the identity's **only** credential. A UAMI cannot hold a secret.
  - Everything lives in the bank's tenant.
- **Why not a vendor multi-tenant app.** A Domino-owned multi-tenant app with FICs would let **any** trusted deployment request tokens for **any** consenting tenant.
- **Token flow.**
  1. ACT reads its Domino run JWT from `localhost:8899/access-token`.
  2. It posts to `login.microsoftonline.com/{tenant}/oauth2/v2.0/token` with `grant_type=client_credentials`, `client_id={uami clientId}`, `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`, `client_assertion={jwt}` and `scope=<audience>/.default`.
  3. It repeats this once per audience: ARM, `ai.azure.com`, Graph, Log Analytics.
- **Token lifetime.** Access tokens last 60-90 minutes and there is **no refresh token**. ACT re-exchanges with a fresh Domino JWT for each crawl.
- **Subject pinning** (the AWS finding, applied to Entra):
  - The FIC matches `issuer`, `subject` and `audiences` **exactly**. Wildcards are not supported.
  - Flexible (pattern) FICs are preview and accept only GitHub, GitLab and Terraform Cloud issuers, so there is no accidental broad match.
  - Set the three values as follows:
    - `issuer` = `https://<domino-host>/auth/realms/DominoRealm`.
    - `subject` = the Keycloak `sub` (GUID) of a **dedicated Domino service account** that publishes the ACT App and launches the crawl Jobs.
    - `audiences` = `[<aud of that token>]`.
  - ACT reads these from its own token claims at runtime (never logged) and pre-fills them.
  - Consequences:
    - Every run as that service account can mint tokens, so lock down who can act as it.
    - If the App runs "as viewer", `sub` changes and the exchange fails with AADSTS700213. Publish it "as the service account".
  - Entra must reach the issuer's discovery document and JWKS over the public internet, and tokens must be RS256 (both verified for se-demo by the AWS researcher).
  - FIC changes take minutes to propagate, so retry on AADSTS70021.
- **Least privilege (Azure RBAC),** assigned at the chosen management group or subscriptions:

  | Role | Id | Covers |
  |---|---|---|
  | **Reader** | `acdd72a7-3385-48ef-bd42-f606fba81ae7` | Resource Graph, accounts, projects, applications, connections metadata, role assignments |
  | Custom **ACT Foundry Inventory Reader** | — | DataActions `Microsoft.CognitiveServices/accounts/AIServices/agents/read`, `…/deployments/read`, `…/connections/read`, `…/managedAgentIdentityBlueprints/read`. The template creates this role. |
  | **Cognitive Services Data Reader** (built-in fallback) | `b59867f0-fa02-499b-be73-45a86b5b3e1c` | DataActions `Microsoft.CognitiveServices/*/read` |
  | Log Analytics Reader (optional, traces only) | `73c42c96-874c-492b-b04d-ab87d138a893` | Trace queries |

  - **Caveat:** `agents/read` also reads threads and messages, and no narrower action exists.
  - Do **not** use Foundry User (`53ca6127…`). Its DataActions are `Microsoft.CognitiveServices/*`, which allows writes.
- **Graph app roles on the UAMI:**
  - `AgentIdentity.Read.All`.
  - `CopilotPackages.Read.All` (only if Agent 365 is licensed).
  - Optional: `AgentIdentityBlueprint.Read.All` and `User.ReadBasic.All` (resolve owner names).
  - Granting these needs a Privileged Role Administrator or Global Administrator.
- **Provisioning artifact: `act-microsoft-readonly.bicep`.** It creates:
  - The UAMI.
  - The FIC (`issuer`, `subject`, `audience` parameters).
  - The custom role and the role assignments (management-group or subscription scope).
  - The Graph app role assignments, through the **Microsoft Graph Bicep extension** (`Microsoft.Graph/appRoleAssignedTo`, GA July 2025).
  - Output `returnUrl = <ACT>/connect/microsoft/callback?tenant=…&client=…&nonce=…`.
- **How the user deploys it.** Azure has **no AWS-style quick-create parameter pre-fill**: "Deploy to Azure" only shows template defaults. So ACT shows a **copy-once, non-secret Cloud Shell command**, for example `az deployment mg create -m <mg> -l eastus --template-uri https://<public>/act-microsoft-readonly.json -p issuer=… subject=… audience=… nonce=…`.
  - Fallback: a portal "Deploy to Azure" button for the ARM-only part, with three copy buttons for the values. Whether the portal handles Graph-extension templates is unverified.
- **Auto-verification.** On callback, ACT:
  1. Performs the token exchange for each audience.
  2. Runs a Resource Graph count query and `GET /subscriptions`.
  3. Calls one Foundry `GET /agents?limit=1` per account.
  4. Calls Graph `agentIdentity?$top=1` and `packages?$top=1`.
  5. Shows a green tick or a fix per capability.

### 4.2 Fallback: Sign in with Microsoft (delegated)

- **Client app.** Domino registers one multi-tenant app.
  - Delegated scopes: ARM `user_impersonation` (resource `797f4846-ba00-4fd7-ba43-dac1f8f63013`), `https://ai.azure.com/user_impersonation`, Graph `AgentIdentity.Read.All`, `CopilotPackages.Read.All`, `offline_access`.
  - One refresh token can mint tokens for all three resources.
  - The Graph `*.Read.All` scopes need admin consent: `https://login.microsoftonline.com/{tenant}/adminconsent?client_id=…`.
- **Redirect URI.** Must be registered exactly and use HTTPS, for example `https://se-demo.domino.tech/apps/<app-id>/api/connect/microsoft/callback`.
  - A path behind the proxy is fine. Wildcards are not allowed, and there is a 256-URI limit.
  - Domino must add each deployment's URL to the app, or run a small redirect broker.
  - For a code exchange without a secret, use the public-client platform with PKCE. Alternatively, run a confidential client whose credential is a FIC on the Domino JWT.
  - Use a popup so the wizard keeps its state.
- **RBAC.** None to add: ACT sees exactly what the signed-in user can see.
- **Weaknesses.**
  - Listing agent identities also needs the **Agent ID Administrator** directory role.
  - The connection breaks when that user leaves, changes password or hits Conditional Access sign-in frequency (AADSTS70043, AADSTS50173).
- **When to use it:** pilots, demos, and private Domino deployments whose Keycloak issuer Entra cannot reach.

---

## 5. Auto-discovery with no typing

1. **Tenant.** WIF: the tenant comes from the provisioning return link. Delegated: `GET /tenants`, with one checkbox per tenant the user can see.
2. **Subscriptions.** `GET /subscriptions` gives everything the identity can read. All are selected by default.
3. **Foundry resources.** One Resource Graph query across all of them:
   ```kql
   resources | where type =~ 'microsoft.cognitiveservices/accounts' and kind =~ 'AIServices'
   | project id, name, subscriptionId, resourceGroup, location, tags, sub=tostring(properties.customSubDomainName),
     foundryApi=tostring(properties.endpoints['AI Foundry API']), projectsOn=tobool(properties.allowProjectManagement),
     network=tostring(properties.publicNetworkAccess)
   ```
   Also count `microsoft.app/agents` (SRE) and hub-based `microsoft.machinelearningservices/workspaces` (legacy hubs, reported but not scanned in v1).
4. **Projects.**
   - Try Resource Graph `type =~ 'microsoft.cognitiveservices/accounts/projects'`. It is not in the supported-tables list, so it is unverified.
   - Otherwise, `GET …/accounts/{a}/projects` per account, in parallel within the quota.
5. **Project endpoint.** `https://{customSubDomainName}.services.ai.azure.com/api/projects/{projectName}`, or the project's `properties.endpoints` when present. **The user never sees or types it.**
6. **Agent counts.** `GET /agents?limit=100` and `/assistants?limit=1` per project.
   - If an account has `publicNetworkAccess=Disabled` and the call fails, label the project "private network: ARM-level only" and keep going.
7. **Graph sweep.** One tenant-wide sweep of agent identities and packages. Join them to Foundry agents on `instance_identity.principal_id = agentIdentity.id = package.agentIdentityId`.

The result is a scope tree with counts: tenant → subscription → Foundry resource → project (N agents), plus "Entra-only agents (M)" and "M365 catalog (K)".

---

## 6. Wizard steps and failures

| Step | User does | ACT does |
|---|---|---|
| 1 Choose | Clicks the **Microsoft** card ("Foundry · Entra Agent ID · Agent 365"). Chooses scope: *one subscription* or *a management group*. Toggles: Agent 365 catalog (on), traces (off). | Reads its token claims and builds the nonce and the Cloud Shell command |
| 2 Grant | Clicks **Copy command**, then **Open Cloud Shell**, pastes and runs it (an admin does this). The output ends with **Return to ACT**. | Polls for the callback |
| 3 Verify | Nothing to do | Per-audience token exchange and capability checks (section 4.1) |
| 4 Scopes | Unticks anything unwanted (everything is pre-selected) | Section 5 tree with counts |
| 5 Crawl → 6 Review → 7 Import → 8 Sync | As in `import-wizard-plan.md` | `discover()` runs as a Domino Job **as the service account** |

The Fallback link on step 2 reads "Just sign in instead". It opens a popup with the consent screen, then goes straight to step 3.

| Error | Where | Plain-language fix |
|---|---|---|
| AADSTS70021 (no matching federated identity record) | Verify | "Azure hasn't finished saving the trust yet. ACT retries for 5 minutes. If it persists, re-run the command." |
| AADSTS700211 / 700212 / 700213 (issuer / audience / subject mismatch) | Verify, Sync | "The trust points at a different Domino deployment / audience / identity. Re-run the command, and check the crawl runs as the ACT service account." |
| AADSTS700024 (assertion not within its valid time range) | Sync | "The Domino token expired before use. ACT fetched a fresh one. If it repeats, check the Domino clock." |
| AADSTS90002 / AADSTS700016 (tenant or app not found) | Verify | "That tenant or identity no longer exists. Reconnect." |
| AADSTS65001 / AADSTS90094 (consent missing or admin required) | Sign-in | "An admin must approve ACT once. Send them this consent link." |
| AADSTS50011 (redirect URI mismatch) | Sign-in | "This ACT address isn't registered with the Microsoft app. Use the secretless setup, or ask Domino to add the URL." |
| AADSTS53003 / AADSTS50076 (Conditional Access / MFA) | Sign-in | "Your organization's sign-in policy blocked this. Use the secretless setup." |
| AADSTS70043 / AADSTS700082 / AADSTS50173 (refresh token expired or revoked) | Sync (delegated) | "Your Microsoft sign-in expired. Sign in again, or switch to the secretless setup." |
| ARM `AuthorizationFailed` (403) | Scopes | "The identity can't read subscription X. Assign Reader there (the command does this)." |
| Resource Graph 429 `RateLimiting` | Crawl | Automatic back-off. No action needed. |
| Foundry 401/403 `PermissionDenied`, missing data action `…/agents/read` | Verify, Crawl | "ACT can see the Foundry resource but not its agents. Assign ACT Foundry Inventory Reader on it." |
| Foundry 403 "public access disabled / not from approved private endpoint" | Crawl | "This Foundry resource is private. ACT lists it from Azure metadata only. To see agents, peer Domino to its network." |
| Graph 403 `Authorization_RequestDenied` | Verify | "Directory read wasn't granted. A Privileged Role Admin must run the Graph part of the command." |
| Graph packages 403 (no licence) / 424 Failed Dependency (reported) | Verify | "Agent 365 catalog needs an Agent 365 licence. Foundry and Entra still work." |

---

## 7. Intake of one agent: extra calls

1. `GET /agents/{name}` and `GET /agents/{name}/versions` (all versions, for history and drift).
2. `GET /deployments` (cached per project): resolve the model to its publisher and version.
3. `GET /connections` (cached): resolve each `project_connection_id` to its type and `target`.
4. Graph `GET /servicePrincipals/{principal_id}`, plus `/owners`, `/sponsors` and `/appRoleAssignments`.
5. ARM role assignments `assignedTo('{principal_id}')` at each selected subscription: actual data access, for example `Storage Blob Data Reader@sa-kyc`.
6. Package detail, if `agentIdentityId` matches: hosts, `deployedTo`, `elementDetails`.
7. Optional: a 30-day trace aggregate (runs, failures, last run), with no content.
8. Redact (section 2.7), then store `raw`.

---

## 8. Recorded-HTTP fixtures for contract tests

Each fixture is stored under `fixtures/http/microsoft/` with its request, status, headers and redacted body.

- **Entra**
  - `entra/token-fic-ok.json`
  - `entra/token-aadsts70021.json`
  - `entra/token-aadsts700213.json`
  - `entra/token-aadsts700024.json`
  - `entra/token-aadsts65001.json` (delegated)
- **ARM**
  - `arm/subscriptions-page1.json` (with `nextLink`), `arm/subscriptions-page2.json`
  - `arm/tenants.json`
  - `arm/arg-foundry-accounts-p1.json` (with `$skipToken`), `arm/arg-foundry-accounts-p2.json`
  - `arm/arg-429.json` (quota headers)
  - `arm/projects-list.json`
  - `arm/applications-list.json`
  - `arm/connections-list.json`
  - `arm/roleassignments-assignedto.json`
  - `arm/authorizationfailed-403.json`
- **Foundry**
  - `foundry/agents-p1.json` (`has_more:true`), `foundry/agents-p2.json`
  - `foundry/agent-prompt.json` (mcp, openapi, search and a2a tools)
  - `foundry/agent-hosted.json` (env vars, to prove redaction)
  - `foundry/agent-workflow.json`
  - `foundry/agent-versions.json`
  - `foundry/assistants-classic.json` (with `connected_agent`)
  - `foundry/deployments.json`
  - `foundry/connections.json`
  - `foundry/403-missing-dataaction.json`
  - `foundry/403-private-network.json`
  - `foundry/400-api-version.json`
  - `foundry/429.json`
- **Graph**
  - `graph/agentidentities-p1.json` (`@odata.nextLink`), `graph/agentidentities-p2.json`
  - `graph/agentidentity-owners.json`
  - `graph/agentidentity-sponsors.json`
  - `graph/packages-list.json` (the **real** shape; replaces `fixtures/microsoft/list-packages.json`)
  - `graph/package-detail.json`
  - `graph/packages-403-nolicence.json`
  - `graph/403-request-denied.json`
  - `graph/429-retry-after.json`
- **Traces:** `loganalytics/invoke-agent-aggregate.json`
- **Optional:** `powerplatform/inventory-copilotstudio.json`

---

## 9. Risks, unknowns, and sources

### Risks and unknowns

1. **Domino token shape is unverified for Entra.**
   - Entra's FIC takes one audience and needs an exact match. If the Keycloak token's `aud` is an array or varies, the exchange can fail.
   - The `sub` must be stable: the App must run as the service account, and Jobs must be launched as it.
   - Private Domino deployments whose issuer Entra cannot reach cannot use WIF; they need the delegated fallback.
   - **Decode the claims once inside a run (never log them) before building.**
2. **Private networking.** Banks commonly set `publicNetworkAccess=Disabled` on Foundry. Then the data plane (agents, tools, identity) is unreachable from Domino unless the networks are peered with private DNS. ARM and Graph still work. The design must degrade per project.
3. **Read-only data access is coarse and partly unverified.**
   - `agents/read` also grants threads and messages, so ACT is technically able to read customer conversations.
   - Whether `Cognitive Services Data Reader` or the custom role is honoured by the current-model Agents API is unverified (forum reports describe wildcard quirks). **Test this in a sandbox first.**
4. **Preview surface.**
   - Preview pieces: Agent Applications ARM (2026-01-15-preview), draft versions, workflow/external/voice kinds and their tracing, and Graph Bicep in the portal.
   - Classic agents retire 2027-03-31.
   - The data-plane version string (`v1` vs `2025-11-15-preview`) is inconsistent across sources.
   - Resource Graph coverage of `accounts/projects` is unverified.
5. **Licence gate.** The Agent 365 package API needs an Agent 365 licence (sources disagree), and app-only calls have reportedly returned 424. Treat it as optional enrichment.
6. **No creator or owner in the Foundry data plane.** Owner quality depends on Entra owners and sponsors, which Foundry does not set automatically.
7. **No public Foundry Control Plane API.** ACT reimplements fleet discovery; it may drift from what the portal shows.
8. **Copilot Studio.**
   - The same consent does **not** reach Copilot Studio drafts. The Power Platform inventory API is delegated-only and needs a Power Platform Administrator.
   - Dataverse `bots` needs per-environment access.
   - Published Copilot Studio agents still arrive through the package catalog (`platform eq 'Copilot Studio'`).
   - Treat drafts as a later, optional toggle.

### Agent 365 recommendation

- **Use one Microsoft connection and one admin action** (the provisioning command, or a single consent) that grants ARM, Foundry data plane and Graph.
- ACT shows three capability rows: **Azure AI Foundry**, **Entra Agent ID** and **Agent 365 catalog** (licence-gated, optional).
- Merge records on the agent identity id.
  - The Foundry agent is the canonical record when one exists.
  - Graph and package data enrich it.
  - Identity-only and package-only agents become their own records.
- Rename the gallery card from "Microsoft Agent 365" to **"Microsoft (Foundry · Entra · Agent 365)"**.

### Sources (all accessed 2026-11-12)

- Foundry REST reference (base URL, `api-version=v1`, `Foundry-Features`, `https://ai.azure.com/.default`): https://ai.azure.com/api-reference
- List agents: https://ai.azure.com/api-reference/agents/list-agents/
- Get agent: https://ai.azure.com/api-reference/agents/get-agent/
- Get agent version: https://ai.azure.com/api-reference/agent-versions/get-agent-version/
- List agent versions: https://ai.azure.com/api-reference/agent-versions/list-agent-versions/
- Tool schemas (generate agent): https://ai.azure.com/api-reference/llm/agents/generate-agent.md
- Agent Applications list (ARM): https://learn.microsoft.com/en-us/rest/api/microsoftfoundry/accountmanagement/agent-applications/list?view=rest-microsoftfoundry-accountmanagement-2026-01-15-preview
- Projects list (ARM): https://learn.microsoft.com/en-us/rest/api/aifoundry/accountmanagement/projects/list
- Foundry Control Plane, manage agents: https://learn.microsoft.com/en-us/azure/foundry/control-plane/how-to-manage-agents
- Foundry RBAC: https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry
- Built-in AI roles: https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/ai-machine-learning
- AI permissions (data actions): https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/ai-machine-learning
- Foundry agent identity: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity
- Agent tracing: https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept
- Entra Agent ID Graph overview: https://learn.microsoft.com/en-us/graph/api/resources/agentid-platform-overview
- agentIdentity resource: https://learn.microsoft.com/en-us/graph/api/resources/agentidentity?view=graph-rest-1.0
- List agentIdentity: https://learn.microsoft.com/en-us/graph/api/agentidentity-list?view=graph-rest-1.0
- List Copilot packages: https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/api/admin-settings/package/copilotpackages-list
- copilotPackage resource: https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/api/admin-settings/package/resources/copilotpackage
- Get package detail: https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/api/admin-settings/package/copilotpackagedetail-get
- MC1297981 (agentRegistry retirement): https://mc.merill.net/message/MC1297981
- MC1173195 (agent and app management APIs): https://mc.merill.net/message/MC1173195
- Topedia (licence, admin role): https://blog.topedia.com/posts/retirement-of-the-agentregistry-api-and-new-licensing-requirements-for-the-agent-365-graph-api
- Workload identity federation considerations: https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-considerations
- Flexible FIC (preview): https://learn.microsoft.com/entra/workload-id/workload-identities-flexible-federated-identity-credentials
- Graph Bicep GA: https://devblogs.microsoft.com/identity/bicep-templates-for-microsoft-entra-id-resources-is-ga/
- Graph Bicep `appRoleAssignedTo`: https://learn.microsoft.com/en-us/graph/templates/reference/approleassignedto
- Deploy to Azure button: https://learn.microsoft.com/azure/azure-resource-manager/templates/deploy-to-azure-button
- Resource Graph supported tables: https://learn.microsoft.com/en-us/azure/governance/resource-graph/reference/supported-tables-resources
- Power Platform inventory API: https://learn.microsoft.com/power-platform/admin/inventory-api
- ServiceNow AI Control Tower, APIs used by its Microsoft connector (competitor reference): https://www.servicenow.com/docs/r/intelligent-experiences/ai-control-tower/apis-used-by-amazon-connector.html
- Foundry MCP Server, available tools: https://learn.microsoft.com/azure/foundry/mcp/available-tools
- Classic agents retirement: https://learn.microsoft.com/en-my/Azure/foundry/how-to/navigate-from-classic
