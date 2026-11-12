# Provider research: Google agents (Gemini Enterprise + Agent Registry)

Researched 2026-11-12. Research only, no app code. A separate researcher covers Vertex AI Agent Engine, which Google now calls **Agent Runtime**. This file covers the other Google surface and recommends **one Google connection for both**.

## 0. Verdict

- Since 2026-04-22, Google's agent stack is **Gemini Enterprise Agent Platform**, and "Vertex AI is now part of Gemini Enterprise Agent Platform". In that release, "Agent Engine is now Agent Runtime", and the `aiplatform.googleapis.com` `reasoningEngines` resources are unchanged.
- **"Google's agent thing" has two parts. A bank risk team means both:**
  1. **Gemini Enterprise**, formerly Google Agentspace. This is the employee-facing app. Its Agents gallery holds Google-made agents (Deep Research, Data Science, and so on), employee-made agents from **Workflow Builder** (formerly Agent Designer, GA 2026-09-03), and admin-registered ADK, A2A and Dialogflow agents. API: `discoveryengine.googleapis.com` `...engines/*/assistants/*/agents/*`, **v1alpha only**.
  2. **Agent Registry** (GA in June 2026, v1 API). This is the "Govern" catalog of agents, MCP servers, endpoints and skills across runtimes: Agent Runtime, Cloud Run, GKE and manually registered external agents. API: `agentregistry.googleapis.com/v1`. **The repo's fixtures model this, but with the wrong shapes (see §3).**
- **Workspace Studio flows** (Workspace agents) have no public inventory API. Admins see them only in the Admin console under Agents > Agent access management. Mark them out of scope for v1 and note them as a gap.
- **One connection:** yes. Use one Google connection with one consent and one service account covering Agent Runtime (Vertex), Agent Registry and Gemini Enterprise. The Google gallery card should be called "Google Cloud (Gemini Enterprise & Agent Platform)", with the Vertex research plugged in as a third resource family.

## 1. What counts as an "agent"

| Product / surface | Resource type and name pattern | Stage | Relationship to Agent Runtime (Vertex) |
|---|---|---|---|
| Agent Registry **Agent** (read-only, system-generated) | `projects/{p}/locations/{loc}/agents/{agent}`, with `agentId` as a URN such as `urn:agent:projects-123:projects:123:locations:us-central1:aiplatform:reasoningEngines:1234` | GA, v1 (2026-06) | Agent Runtime agents deployed with the SDK auto-register. `attributes["agentregistry.googleapis.com/system/RuntimeReference"].uri` points to the reasoningEngine. |
| Agent Registry **Service** (user-defined) | `.../services/{svc}` with `agentSpec`, `mcpServerSpec` or `endpointSpec` | GA v1 | Used to register remote or external agents manually. Creating one produces a read-only `Agent` (`registryResource`). |
| Agent Registry **McpServer**, **Endpoint**, **Binding**, **AiApplication**, **Finding** | `.../mcpServers/*`, `.../endpoints/*`, `.../bindings/*`, `.../aiApplications/*`, `.../findings/*` | v1 (AiApplication and Finding appear in the live discovery doc, 2026-11-12) | Bindings link agents to MCP servers, endpoints and auth providers (tool and data dependencies). Findings are vulnerability or secret scan results. |
| Agent Registry **Skill** | `.../skills/*`, `/revisions/*` | v1alpha only | Reusable skills. Optional for ACT. |
| Gemini Enterprise **app** (Engine) | `projects/{p}/locations/{global\|us\|eu}/collections/default_collection/engines/{engine}` | GA product | `Engine.associatedAgentRegistry` (output only) links the app to its Agent Registry. `agentGatewaySetting` links it to Agent Gateway. |
| Gemini Enterprise **Assistant** | `.../engines/{e}/assistants/default_assistant` | GA product; API v1 and v1alpha | Holds `enabledTools`, keyed by data connector (these are the app's "actions"). |
| Gemini Enterprise **Agent** | `.../assistants/{a}/agents/{agent}`. Kind is given by one of `adkAgentDefinition` (points to `reasoningEngines/*`), `a2aAgentDefinition` (`jsonAgentCard`), `dialogflowAgentDefinition`, `managedAgentDefinition` (Google-managed, for example Data Science with a BigQuery dataset), or none (Workflow Builder) | Product GA. A2A and A2UI registration went GA 2026-08-17 and Workflow Builder GA 2026-09-03. **The agents REST API is v1alpha only** (v1 has only the A2A `getCard`/`message` paths). | An ADK agent here is a **Gemini Enterprise registration of an Agent Runtime agent**. The same agent appears in both places, so ACT must dedupe on the reasoningEngine name (§7). |

Release dates: Agent Registry was Public Preview 2026-04-22 and reached GA with the v1 API in June 2026. The Agent Identity API (`agentidentity.googleapis.com`) is in **Preview**, but Agent Identity itself is GA. Agent Gateway reached GA on 2026-06-18. On 2026-10-28 Google advised "associate each Agent Gateway with a single regional Agent Registry instance" (us-central1 for global/US apps, europe-west1 for EU apps).

## 2. Inventory APIs

Base URLs: `https://agentregistry.googleapis.com`, `https://discoveryengine.googleapis.com`. All calls use `Authorization: Bearer <access token>`. ACT should send `x-goog-user-project: <project>` only when it uses user OAuth.

### 2a. Agent Registry (v1, GA). This is the primary inventory.

| Call | Notes |
|---|---|
| `GET /v1/projects/{p}/locations` | Discovers locations. Registry locations are `global`, the multi-regions `us` and `eu`, and about 40 regions. |
| `GET /v1/projects/{p}/locations/{loc}/agents?pageSize&pageToken&filter&orderBy` | Returns `{agents[], nextPageToken}`. Permission `agentregistry.agents.list`. Scopes: `agentregistry.read-only` **or** `cloud-platform.read-only` both work. |
| `GET /v1/{name=.../agents/*}` | One agent. |
| `POST /v1/.../agents:search` `{searchString, pageSize<=100 (default 20), pageToken}` | Keyword search over agentId, name, displayName, description and skills.* |
| `GET /v1/.../mcpServers` / `endpoints` / `services` / `bindings` | Lists each resource type. `bindings` defaults to and caps at 500 per page. `services` and `endpoints` take AIP-160 filters. |
| `GET /v1/.../bindings:fetchAvailable?sourceIdentifier=urn:agent:...` | Returns the bindings that apply to one agent. Use it for per-agent tool and data dependencies. |
| `GET /v1/.../aiApplications`, `.../findings` | App Hub applications (`{aiApplications[], unreachable[]}`) and security findings (`severity`, `findingClass` VULNERABILITY/SECRET, `resource`). |

```json
{"agents":[{"name":"projects/bank-ai-prod/locations/us-central1/agents/agt-7f3",
  "agentId":"urn:agent:projects-4417:projects:4417:locations:us-central1:aiplatform:reasoningEngines:1220771026",
  "uid":"2b0c…","location":"us-central1","displayName":"payments-fraud-screener",
  "description":"Scores real-time payments…","version":"1.4.0",
  "protocols":[{"type":"A2A_AGENT","protocolVersion":"1.0","interfaces":[{"url":"https://…","protocolBinding":"JSONRPC"}]}],
  "skills":[{"id":"hold_payment","name":"Hold payment","description":"…","tags":["payments"],"examples":[]}],
  "attributes":{"agentregistry.googleapis.com/system/Framework":{"framework":"google-adk"},
    "agentregistry.googleapis.com/system/RuntimeIdentity":{"principal":"principal://agents.global.org-123.system.id.goog/…"},
    "agentregistry.googleapis.com/system/RuntimeReference":{"uri":"//aiplatform.googleapis.com/projects/4417/locations/us-central1/reasoningEngines/1220771026"}},
  "card":{"type":"A2A_AGENT_CARD","content":{}},
  "createTime":"2026-10-02T09:00:00Z","updateTime":"2026-11-01T08:12:09Z"}],
 "nextPageToken":""}
```
Binding (v1). Note `target.identifier` is a **URN**, and `authProviderBinding` holds an `authProvider`, **not a service account**:
```json
{"name":"projects/p/locations/us-central1/bindings/fraud-to-payments-mcp","source":{"identifier":"urn:agent:…"},
 "target":{"identifier":"urn:mcp:…:payments-hub"},"authProviderBinding":{"authProvider":"projects/p/locations/us-central1/authProviders/payments-oauth","scopes":["payments.write"]},
 "createTime":"…","updateTime":"…"}
```
McpServer contains `tools[]` (`name`, `description`, `annotations`, `_meta`), so ACT can read write and destructive hints from MCP annotations.

### 2b. Gemini Enterprise (Discovery Engine v1alpha; product GA, API alpha)

| Call | Notes |
|---|---|
| `GET /v1alpha/projects/{p}/locations/{global\|us\|eu}/collections` | Usually only `default_collection`. |
| `GET /v1alpha/.../collections/{c}/engines` | Gemini Enterprise apps. Fields: `displayName`, `solutionType` (agent apps are `SOLUTION_TYPE_SEARCH` with `appType` or `SOLUTION_TYPE_CHAT`), `dataStoreIds[]`, `associatedAgentRegistry`, `agentGatewaySetting`, `observabilityConfig`, `modelConfigs`, `features`, `createTime`, `updateTime`. |
| `GET /v1alpha/.../engines/{e}/assistants` | Usually `default_assistant`. `enabledTools` maps data connector name to the enabled actions. |
| `GET /v1alpha/.../assistants/{a}/agents?pageSize(≤1000, default 100)&pageToken&filter&orderBy` | Filter fields: `displayName`, `id`, `state`, `createTime`, `updateTime`. Order by `updateTime` or `isPinned`. **Docs say "created by the caller"**, while the response doc says "agents visible to the caller". See Risk R1. |
| `GET /v1alpha/{name=.../agents/*}` | One agent. |
| `GET /v1/.../agents/{id}/a2a/v1/card` | Agent card (v1) for A2A agents. |
| `GET /v1alpha/.../collections/{c}/dataConnector` (`getDataConnector`) | `dataSource` (for example `salesforce`, `jira`, `confluence`, `gcs`), `entities[]`, `actionConfig`, `dynamicTools[]`, `connectorModes`, `state`, `egressFqdns`, `lastSyncTime`. Use this for data scopes and actions. |
| `GET /v1alpha/.../engines/{e}:getIamPolicy`, `collections/{c}:getIamPolicy` | Who can use the app. |

OAuth scope caveat: Discovery Engine methods accept **only** `cloud-platform`, `discoveryengine.readwrite` or `discoveryengine.serving.readwrite`. **They do not accept `cloud-platform.read-only`.** Read-only access must therefore come from IAM roles, not from the token scope.

```json
{"agents":[{"name":"projects/bank-ai-prod/locations/global/collections/default_collection/engines/ge-bank/assistants/default_assistant/agents/1784401",
  "displayName":"Collections letter drafter","description":"Drafts hardship letters…",
  "state":"ENABLED","sharingConfig":{"scope":"RESTRICTED"},
  "adkAgentDefinition":{"provisionedReasoningEngine":{"reasoningEngine":"projects/bank-ai-prod/locations/us-central1/reasoningEngines/401391792873"}},
  "authorizationConfig":{"toolAuthorizations":["projects/4417/locations/global/authorizations/sf-oauth"]},
  "observabilityConfig":{"observabilityEnabled":true,"sensitiveLoggingEnabled":false},
  "starterPrompts":[{"text":"Draft a hardship letter"}],"createTime":"2026-09-10T…","updateTime":"2026-11-02T…"}],
 "nextPageToken":"…"}
```
`state` values: CONFIGURED, CREATING, CREATION_FAILED, DEPLOYING, DEPLOYMENT_FAILED, PRIVATE, ENABLED, DISABLED, SUSPENDED. Matching reasons are in `suspensionReason`, `rejectionReason`, `deploymentFailureReason` and `creationFailureReason`. `sharingConfig.scope` is RESTRICTED (by IAM), ALL_USERS or PRIVATE.

### 2c. Reading each dimension

| Dimension | Agent Registry | Gemini Enterprise |
|---|---|---|
| Tools / actions | `skills[]` from the A2A card; bindings to `mcpServers` and their `tools[]` | `authorizationConfig.toolAuthorizations`; assistant `enabledTools` (app-wide); `dataConnector.actionConfig`/`dynamicTools`. **Workflow Builder agents' steps and tools are not in the public schema.** |
| Data stores / connectors | Bindings to endpoints and MCP servers | `engine.dataStoreIds[]`; `dataConnector.dataSource` and `entities[]`; `managedAgentDefinition.dataScienceAgentConfig.{bqProjectId,bqDatasetId,allowlistTables}` |
| Model | Not exposed. Get it from the Agent Runtime spec (Vertex researcher). | `engine.modelConfigs` (app-level allow-list). Not exposed per agent. |
| Identity | `attributes…/RuntimeIdentity.principal` (Agent Identity SPIFFE principal or SA) | For ADK agents, follow the reasoningEngine's identity. Admin UI shows SPIFFE ID or Registry ID. |
| Sharing / permissions | `aiApplications:getIamPolicy` | `sharingConfig.scope`; `engines:getIamPolicy`. The `discoveryengine.agents.getIamPolicy` permission exists (in Viewer), **but the method is not in the public discovery doc** (R2). |
| Owner / creator | None. Use labels on Services or on the reasoningEngine. | No owner field in the API. The console has "Transfer ownership" (2026-09-21). Fallback: Cloud Audit Logs `CreateAgent` `protoPayload.authenticationInfo.principalEmail`. |
| Versions | `version` (from the A2A card) | None. Use `updateTime` as a version. |
| Status / times | `createTime` and `updateTime` (no status) | `state`, reasons, `createTime`, `updateTime` |

### 2d. Usage and trace APIs

- **Cloud Logging** (`logging.googleapis.com/v2/entries:list`): Gemini Enterprise agent and observability logs, and Cloud Audit Logs for `discoveryengine.googleapis.com` (`StreamAssist`, `CreateAgent`, `UpdateAgent`). Data Access audit logs need `roles/logging.privateLogViewer`, and the bank must enable them.
- **Cloud Trace** (`cloudtrace.googleapis.com` v1 `projects.traces.list`, and the Telemetry API used for OTel): ADK on Agent Runtime traces by default (GA 2026-06-18). Agent Gateway → Cloud Trace is in Preview (2026-09-30). Gemini Enterprise "experimental agent telemetry (OpenTelemetry)" dates from 2026-08-07. Share this with the Vertex trace ingest.
- **Gemini Enterprise analytics**: `POST .../engines/{e}/analytics:exportMetrics` writes to BigQuery or GCS (a **write** call with up to 180 days of retention). Not for v1. The Observability tab (latency and error views, GA 2026-09-03) has no public read API.
- **Cloud Monitoring**: Agent Runtime `gen_ai` metrics (ADK ≥ 2.6.0) and `discoveryengine.googleapis.com/dataconnector/request_latencies` (Beta).

### 2e. MCP

There is an **Agent Registry remote MCP server** at `https://agentregistry.googleapis.com/mcp` (streamable HTTP, OAuth/IAM, `roles/mcp.toolUser` plus `roles/agentregistry.viewer`). Its tools include `list_agents`, `get_agent`, `search_agents`, `list_mcp_servers`, `list_bindings`, `fetch_available_bindings` and others, plus write tools. **It is not recommended for ACT:** it accepts only the `cloud-platform` or `agentregistry.read-write` scopes, it offers nothing the REST API doesn't, and the REST API is easier to record for contract tests. Discovery Engine has no MCP server for agent inventory.

### 2f. Quotas

Google does not publish specific numbers for agentregistry or agents.list reads. Expect per-project, per-minute read quotas visible in Console > Quotas. ACT should page sequentially, back off exponentially on `429 RESOURCE_EXHAUSTED`, and cap at about 5 QPS per project.

## 3. Field mapping to `SourceAgent`

Precedence: Agent Registry first, then Gemini Enterprise, deduplicated by reasoningEngine (§7).

| SourceAgent | Agent Registry Agent | Gemini Enterprise Agent |
|---|---|---|
| platform | `"google"` | `"google"` |
| native_id | `agentId` (stable URN) | full `name`. If `adkAgentDefinition` is present, the key is the reasoningEngine name. |
| name / description | `displayName` / `description` | `displayName` / `description` |
| version | `version` or `"1"` | `"1"`, plus `updateTime` in versions |
| owner | Service `labels.owner` or the reasoningEngine label | Audit-log creator (from `CreateAgent`) |
| status | `"READY"` (no status field) | `state` (ENABLED→active; SUSPENDED/DISABLED→inactive; *_FAILED→error; PRIVATE→draft) |
| last_updated | `updateTime` | `updateTime` |
| url | `protocols[0].interfaces[0].url` | Console deep link `https://console.cloud.google.com/gemini-enterprise/…` (build it, never type it) |
| provider | `"Google Cloud – Agent Registry"` | `"Google – Gemini Enterprise"` |
| tools | `skills[].id` plus `tools[].name` of bound MCP servers | `authorizationConfig.toolAuthorizations`, `enabledTools`, `dynamicTools[].name` |
| data_scopes | Bound endpoint and MCP URNs | `dataStoreIds`, `dataConnector.dataSource`/`entities`, BigQuery dataset/tables |
| systems | Hosts of interface URLs; MCP server display names | Connector `dataSource` names (Salesforce, Jira…) |
| value_limits | none (leave `{}`) | none |
| criteria | `{framework, protocol}` | `{sharing: sharingConfig.scope, kind: adk/a2a/dialogflow/managed/workflow, sensitiveLogging}` |
| identity | `{type: "agent_identity"\|"service_account", principal: RuntimeIdentity.principal, distinct: principal not a default SA}` | Inherited from the reasoningEngine, otherwise `{type:"google_managed"}` |
| versions | `[{version, created: createTime}]` | `[{version: updateTime, created: createTime}]` |
| dependencies | `{mcp_servers: [binding targets urn:mcp:*], agents: [urn:agent:*]}` | `{models: engine.modelConfigs keys}` |
| extra | `{registry_name, runtime_ref, findings: count by severity}` | `{engine, assistant, kind, reasons}` |

**Fixture gap:** `fixtures/google/*.json` and `adapters/google.py` invent `Service.properties.{tools,data_scopes,value_limits,criteria}`, `Service.identity`, `bindingsByService` and `binding.target.agentEngine`/`authProviderBinding.serviceAccount`. **None of these exist in the real API.** T-45 should re-record the fixtures as `agents`/`bindings`/`mcpServers` pages and move the bank-specific risk hints to Service `labels` (or ACT-side overrides).

## 4. Auth options, ranked by friction (all shared with Vertex)

### A. Recommended primary: Workload Identity Federation from Domino's OIDC token, set up by a one-paste Cloud Shell script

- **User steps (about 60 s, nothing typed):**
  1. Click **Set up in Cloud Shell**. ACT copies a self-contained command (base64-embedded script with the issuer, audience, subject condition and a nonce) and opens `https://shell.cloud.google.com/?show=terminal` in a new tab.
  2. Paste and press Enter. The script shows an arrow-key picker of org, folders and projects (`gcloud projects list`/`resource-manager folders list`) for the **scope** and the **host project** for the pool. Confirm with Y.
  3. The script prints a **Return to ACT** link (`https://<domino>/apps/<id>/#/connect/google/done?cfg=<base64 of {projectNumber, pool, provider, sa, scope, nonce}>`). Click it.
- **What the script runs (idempotent):** it enables `sts`, `iamcredentials`, `agentregistry`, `discoveryengine`, `aiplatform`, `cloudresourcemanager` and `cloudasset`. It then runs `gcloud iam workload-identity-pools create act-pool --location=global`, then `providers create-oidc act-domino --issuer-uri=<Keycloak realm URL> --allowed-audiences=<aud from Domino token> --attribute-mapping=google.subject=assertion.sub,attribute.project=assertion.<project claim> --attribute-condition=<pins ACT's Domino project/app>` (with `--jwk-json-path` when the issuer is not public). It creates the SA `act-reader@<host>.iam.gserviceaccount.com`, grants `roles/iam.workloadIdentityUser` on the SA to `principalSet://iam.googleapis.com/projects/<num>/locations/global/workloadIdentityPools/act-pool/attribute.project/<id>`, and grants the read roles below at org, folder or project scope.
- **Admin setup:** the person running the script needs `roles/iam.workloadIdentityPoolAdmin`, `roles/iam.serviceAccountAdmin`, `roles/serviceusage.serviceUsageAdmin` on the host project, and `setIamPolicy` on the chosen scope (in practice a cloud platform admin).
- **What ACT must register:** nothing with Google. No OAuth client and no verification are needed.
- **Redirect constraints:** none. The Return link is a plain navigation into the App, and ACT checks the nonce.
- **Token lifetime:** the Domino JWT is read fresh per call from `http://localhost:8899/access-token`. GCP requires RS256/ES256 and exp−iat ≤ 24 h. STS exchange (`POST https://sts.googleapis.com/v1/token`, `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `audience=//iam.googleapis.com/projects/<num>/locations/global/workloadIdentityPools/act-pool/providers/act-domino`, `subject_token_type=urn:ietf:params:oauth:token-type:jwt`, `scope=…/cloud-platform`) then `iamcredentials …:generateAccessToken`. The SA token lasts **1 h**. Nothing is long-lived.
- **What ACT stores:** project number, pool and provider IDs, SA email and scope list. **No secrets.**
- **Least-privilege roles for the SA** (grant at org or folder for "all projects", or per project). This combined set covers Vertex too:
  - `roles/agentregistry.viewer`
  - `roles/discoveryengine.viewer`. `roles/discoveryengine.agentspaceViewer` has the same agents.* permissions and is narrower elsewhere.
  - `roles/aiplatform.viewer` (Agent Runtime, the Vertex researcher's surface)
  - `roles/browser` (projects and folders) **or** `roles/cloudasset.viewer` at org or folder (one-call discovery, §5)
  - `roles/serviceusage.serviceUsageConsumer` is **not** needed. `roles/serviceusage.serviceUsageViewer` is enough to see which APIs are enabled.
  - Optional (traces, usage, creator):
    - `roles/logging.viewer`, plus `roles/logging.privateLogViewer` for Data Access audit logs
    - `roles/cloudtrace.user`
    - `roles/monitoring.viewer`
  - Optional (identity reach):
    - `roles/iam.securityReviewer` to read the IAM policies granted to agent principals
- **One-click artifact:** the Cloud Shell command, plus a 40-line Terraform module (`act_google_connect`) for banks that require IaC. Both live in the repo and ACT renders them.
- **Auto-verification:** mint a token, then call `POST cloudresourcemanager.googleapis.com/v3/projects/{p}:testIamPermissions` with `agentregistry.agents.list`, `discoveryengine.agents.list`, `discoveryengine.engines.list`, `aiplatform.reasoningEngines.list`, `logging.logEntries.list` and `cloudtrace.traces.list`. This gives the per-permission ticks. Then probe one `agents.list` per family.
- **Gating:** the Domino Keycloak discovery and JWKS must be reachable from the internet with a public CA cert. If they aren't, the script uploads the JWKS, which allows at most 8 keys and breaks on Keycloak key rotation (R4).

### B. Recommended fallback: "Sign in with Google" popup (the user's own identity)

- **User steps:** click **Sign in with Google**, pick an account and consent. That's 3 clicks.
- **Admin setup:** an OAuth client of type Web application in a GCP project the bank controls. The redirect URI must be an **exact HTTPS match**: `https://se-demo.domino.tech/apps/<app-id>/api/connect/google/callback`. Consent screen **Internal** (bank's Cloud Identity org; no Google verification) is preferred. **External** needs verification because `cloud-platform` is a sensitive scope, and "Testing" mode refresh tokens expire after 7 days. Google Workspace "API controls" may block unconfigured third-party apps (`admin_policy_enforced`).
- **What ACT registers and stores:** client ID and client secret (once, by the admin, in Settings). Per user, an encrypted **refresh token**, which is a long-lived secret.
- **Flow:** Authorization Code with PKCE, `access_type=offline`, `prompt=consent`, `scope=openid email https://www.googleapis.com/auth/cloud-platform`. cloud-platform is required because Discovery Engine does not accept `cloud-platform.read-only` (§2b). IAM limits the actual access. Access tokens last 1 h.
- **Limits:** Google Cloud **session control / reauth policy** (1–24 h) makes refresh fail with `invalid_grant` / `invalid_rapt`, which breaks scheduled sync. Inventory is limited to what the human can see. The Domino App URL must stay stable across versions.
- **Verification:** same as A (`testIamPermissions`). Use B for "quick look now" or where org policy `constraints/iam.workloadIdentityPoolProviders` blocks Domino's issuer. Offer "Convert to Cloud Shell setup" afterwards.

### C. Last resort: service-account key JSON upload

Drag and drop the key file. It is often impossible because `constraints/iam.disableServiceAccountKeyCreation` is enforced by default for orgs created since 2024. It is a long-lived secret, encrypted at rest, and ACT warns to rotate it at 90 days. It uses the same roles and verification.

(Rejected: pasting tokens or ADC files, and Agent Registry MCP as an auth path.)

## 5. Auto-discovery of scopes (no typing)

1. **Org-wide in one call** (if `cloudasset.viewer` is granted at org or folder): `GET cloudasset.googleapis.com/v1/organizations/{org}:searchAllResources?assetTypes=discoveryengine.googleapis.com/Engine,aiplatform.googleapis.com/ReasoningEngine` returns every project and location that has Gemini Enterprise apps or Agent Runtime agents. Agent Registry asset types are not listed in CAI as of 2026-11-12 (R5).
2. **Otherwise:** `POST cloudresourcemanager.googleapis.com/v3/projects:search` (projects where the SA has any role), then `GET serviceusage.googleapis.com/v1/projects/{p}/services?filter=state:ENABLED` and keep projects with `agentregistry`, `discoveryengine` or `aiplatform` enabled.
3. **Locations:** registry `GET /v1/projects/{p}/locations`, intersected with probes on `global`, `us`, `eu` and the regions where reasoningEngines exist. Gemini Enterprise is always `global`, `us` and `eu`.
4. **Apps:** `collections.list` → `engines.list` → `assistants.list` for each Gemini Enterprise location.
5. ACT shows a tree (Org › Project › Registry location / Gemini app) with counts, everything selected by default. If an `associatedAgentRegistry` appears on an engine, ACT also selects that registry.

## 6. Wizard steps and failures

1. **Choose system:** Google Cloud card ("Gemini Enterprise, Agent Registry, Agent Runtime").
2. **Connect:** primary button **Set up in Cloud Shell** (A), secondary **Sign in with Google** (B), link **Upload a key file** (C), and **Use sample tenant**. A shows the copied-command toast and waits. The tab polls for the Return link (nonce).
3. **Verify (automatic):** permission ticks per family (Registry / Gemini Enterprise / Agent Runtime / Logs & Traces) with one-line fixes.
4. **Scopes:** auto-discovered tree, all selected (§5).
5. **Crawl** (Domino Job) → **Review** → **Import**, as in the plan.

The user only clicks: card → Cloud Shell (paste once, choose in a picker, confirm) → Return link → Next → Import. **Zero typed fields.**

| Where | Exact error | Plain-language fix |
|---|---|---|
| Script | `FAILED_PRECONDITION … constraints/iam.workloadIdentityPoolProviders` | "Your org only allows approved identity providers. Ask a GCP org admin to add `<issuer>` to that policy, or use Sign in with Google." |
| Script | `PERMISSION_DENIED iam.workloadIdentityPools.create` | "You need to be a project IAM admin in the host project. Pick another project or ask your cloud team to run this command." |
| STS | `400 invalid_grant` "The audience in ID Token … does not match the expected audience" | "Re-run setup; ACT's token audience changed." (ACT regenerates the script.) |
| STS | `400 invalid_grant` "… JWKS …" / unable to fetch keys | "Google can't reach Domino's sign-in keys. Re-run setup with key upload." |
| STS | `400 unauthorized_client` "The given credential is rejected by the attribute condition" | "This Domino app isn't the one that was authorised. Re-run setup from this app." |
| STS | `400 invalid_target` | "The trust link was deleted or disabled. Reconnect." |
| IAM Credentials | `403 PERMISSION_DENIED iam.serviceAccounts.getAccessToken` | "Grant 'Workload Identity User' on act-reader to ACT (the script line is shown)." |
| Any API | `403 SERVICE_DISABLED` (reason in `error.details[].reason`) | "Gemini Enterprise / Agent Registry isn't turned on in <project>. Skipping it." (Shown as info, not an error.) |
| Any API | `403 IAM_PERMISSION_DENIED` / `PERMISSION_DENIED` | "ACT can't read <family> in <project>. Add role <role> (copy line)." |
| Any API | `404 NOT_FOUND` (location) | Skip the location silently. |
| Any API | `429 RESOURCE_EXHAUSTED` | Back off automatically: "Google is rate-limiting; continuing slowly." |
| Any API | `401 UNAUTHENTICATED` | Mint a fresh token once. If it fails again: "Connection expired, reconnect." |
| Discovery Engine | `403 ACCESS_TOKEN_SCOPE_INSUFFICIENT` | (B only) "Sign in again; ACT needs the Google Cloud scope." |
| OAuth | `Error 400: redirect_uri_mismatch` | "The App URL changed. Add <exact URL> to the OAuth client (copy button)." |
| OAuth | `Error 403: org_internal` / `admin_policy_enforced` / `access_denied` | "Use an account in your bank's Google org" / "Ask the Workspace admin to trust ACT's client ID" / "Consent was cancelled." |
| OAuth refresh | `invalid_grant` (`invalid_rapt`) | "Your org requires periodic re-sign-in. Re-authenticate, or switch to Cloud Shell setup for unattended sync." |
| Key upload | `iam.disableServiceAccountKeyCreation` | "Key files are blocked in your org (good). Use Cloud Shell setup." |

## 7. Intake of one agent (extra calls)

- **Registry agent:**
  1. `agents.get`
  2. `bindings:fetchAvailable?sourceIdentifier=<agentId>`
  3. For each `urn:mcp:*` target, `mcpServers.search`/`get` to get `tools[]` and annotations
  4. `findings.list?filter=resource="<name>"`
  5. If `RuntimeReference` points to a reasoningEngine, hand off to the Vertex adapter's `get` (model, SA, spec)
  6. Optionally `iam.googleapis.com` / `cloudasset searchAllIamPolicies?query=memberTypes:principal AND policy:<principal>` to show what the agent's identity can touch
- **Gemini Enterprise agent:**
  1. `agents.get`
  2. The parent `engines.get` (data stores, models, gateway, registry)
  3. `assistants.get` (`enabledTools`)
  4. `collections.getDataConnector` (sources and actions)
  5. `engines:getIamPolicy` (who can use it)
  6. For A2A agents, `a2a/v1/card`
  7. For ADK agents, the reasoningEngine via the Vertex adapter
  8. Creator: `entries:list` with `protoPayload.methodName:"CreateAgent" AND protoPayload.resourceName:"<agent>"`
- **Dedupe:** one ACT record per reasoningEngine. Merge registry, Gemini Enterprise and Agent Runtime views, with `extra.surfaces=["registry","gemini_enterprise","agent_runtime"]`.

## 8. Recorded-HTTP fixtures for contract tests (`fixtures/http/google/`)

1. `sts_token_exchange_ok.json`, `sts_invalid_grant_audience.json`, `sts_attribute_condition_rejected.json`
2. `iamcredentials_generateAccessToken_ok.json`, `…_403.json`
3. `crm_projects_search_page1.json`, `…_page2.json`, `crm_testIamPermissions_partial.json`
4. `cloudasset_searchAllResources_engines_reasoningEngines.json`
5. `serviceusage_services_enabled.json`
6. `agentregistry_locations_list.json`
7. `agentregistry_agents_list_page1.json` (with `nextPageToken`), `…_page2.json` (with an A2A agent, an ADK/Agent Runtime agent and a CUSTOM agent)
8. `agentregistry_agents_get.json`, `agentregistry_bindings_fetchAvailable.json`, `agentregistry_mcpServers_get_with_tools.json`, `agentregistry_services_list.json`, `agentregistry_findings_list.json`
9. `agentregistry_403_SERVICE_DISABLED.json`, `agentregistry_429.json`
10. `de_collections_list.json`, `de_engines_list.json` (with `associatedAgentRegistry`), `de_assistants_list.json`, `de_getDataConnector.json`
11. `de_agents_list_page1.json` / `page2` (ADK, A2A, Dialogflow, managed Data Science, Workflow Builder, one SUSPENDED and one DEPLOYMENT_FAILED)
12. `de_agents_get.json`, `de_engines_getIamPolicy.json`, `de_a2a_card.json`, `de_403_ACCESS_TOKEN_SCOPE_INSUFFICIENT.json`
13. `logging_entries_CreateAgent.json`, `cloudtrace_traces_list.json`
14. `oauth_token_ok.json`, `oauth_invalid_rapt.json`

## 9. Risks, unknowns, sources

**Risks and unknowns**
- **R1 (high): Gemini Enterprise `agents.list` may return only the caller's agents.** The method text says "created by the caller". A viewer SA could then miss employee-made Workflow Builder agents. Test with `roles/discoveryengine.viewer` versus `agentspaceAdmin`. Mitigation: the registry multi-region (`us`/`eu`) reportedly lists Gemini Enterprise auto-registered agents. One search snippet said so, but the locations page did not confirm it. Otherwise, fall back to Cloud Audit Log enumeration of `CreateAgent`.
- **R2: Gemini Enterprise API is v1alpha, and some methods are hidden.** `agents.getIamPolicy`, `getAgentView` and `listAvailableAgentViews` exist as permissions but not in the public discovery doc. Workflow Builder definitions (steps, tools, model) are not exposed, so tiering for those agents relies on sharing, connectors and the audit log.
- **R3: Domino token fitness for WIF.** Unknowns: the issuer URL, `aud`, and whether a stable claim pins **ACT's project or app**. App runs and crawl Jobs may carry different `sub` values (the user versus the app identity). Inspect the claims (never print the token) before fixing the attribute condition.
- **R4: Issuer reachability and key rotation.** Private or self-signed Keycloak forces JWKS upload (≤ 8 keys), which silently breaks on rotation. ACT should detect `invalid_grant` and offer "refresh keys".
- **R5:** Agent Registry auto-registration is **per project** unless a management boundary (org or folder) is configured. Many banks will have scattered registries. CAI does not yet index registry agents.
- **R6:** The Workforce Identity Federation limitation on Agent Registry ("internal errors") applies to workforce identities, not workload identities. ACT should still use SA impersonation rather than direct `principal://` grants, because Discovery Engine is not listed on the federation support page.
- **R7:** Banks often enforce VPC Service Controls around Agent Platform. Calls from Domino's egress IP will fail with `403 VPC_SERVICE_CONTROLS` unless an ingress rule allows the ACT SA. Add this to the failure table in T-45.
- **R8:** Workspace Studio flows have no public API, so ACT cannot inventory them yet.

**Sources (accessed 2026-11-12)**
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/overview
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/release-notes
- https://cloud.google.com/blog/products/ai-machine-learning/introducing-gemini-enterprise-agent-platform
- https://docs.cloud.google.com/agent-registry/overview
- https://docs.cloud.google.com/agent-registry/reference/rest (and `/v1/projects.locations.agents`, `.services`, `.bindings`, `.agents/list`)
- https://agentregistry.googleapis.com/$discovery/rest?version=v1 and `?version=v1alpha`
- https://docs.cloud.google.com/agent-registry/roles-permissions
- https://docs.cloud.google.com/agent-registry/register-agents
- https://docs.cloud.google.com/agent-registry/automatic-registration
- https://docs.cloud.google.com/agent-registry/locations
- https://docs.cloud.google.com/agent-registry/use-agentregistry-mcp
- https://docs.cloud.google.com/gemini/enterprise/docs/agents-overview
- https://docs.cloud.google.com/gemini/enterprise/docs/share-custom-agents
- https://docs.cloud.google.com/agentspace/docs/release-notes (Gemini Enterprise release notes)
- https://docs.cloud.google.com/gemini/enterprise/docs/reference/rest/v1alpha/projects.locations.collections.engines.assistants.agents (and `/list`)
- https://docs.cloud.google.com/gemini/enterprise/docs/reference/rest
- https://discoveryengine.googleapis.com/$discovery/rest?version=v1alpha and `?version=v1`
- https://docs.cloud.google.com/gemini/enterprise/docs/invoke-agent-streamassist
- https://docs.cloud.google.com/iam/docs/roles-permissions/discoveryengine
- https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-providers
- https://docs.cloud.google.com/iam/docs/federated-identity-supported-services
- https://docs.cloud.google.com/asset-inventory/docs/asset-types
- https://knowledge.workspace.google.com/admin/studio/manage-ws-flows-access-to-data
- https://workspaceupdates.googleblog.com/2026/08/new-enterprise-security-controls-for-Workspace-Studio-enable-expanded-collaboration-use-cases.html
