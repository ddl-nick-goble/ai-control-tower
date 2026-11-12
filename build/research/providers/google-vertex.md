# Provider research: Google Cloud Vertex AI agents (Gemini Enterprise Agent Platform)

Researched 2026-11-12. Research only. Schemas below were taken from Google's live discovery documents (`aiplatform` v1/v1beta1, `agentregistry` v1/v1alpha, `cloudasset` v1, `logging` v2, `cloudtrace` v1, `cloudresourcemanager` v3, `serviceusage` v1), fetched 2026-11-12. "Verify" marks a claim I could not confirm from a primary source.

**Naming.** At Cloud Next on 2026-04-22, Google renamed Vertex AI to **Gemini Enterprise Agent Platform**. "Agent Engine" is now **Agent Runtime** in the docs. The API is unchanged: `aiplatform.googleapis.com`, resource `reasoningEngines`. Docs moved to `docs.cloud.google.com/gemini-enterprise-agent-platform/...`, and older `/agent-builder/...` and `/vertex-ai/...` URLs still resolve. The ACT card should read "Google Cloud: Agent Platform (Vertex AI)".

**Shared auth with Gemini Enterprise (the other researcher's scope).** Gemini Enterprise (formerly Agentspace, API `discoveryengine.googleapis.com`) and **Agent Registry** (`agentregistry.googleapis.com`) use the same Google Cloud IAM, OAuth scopes, and organization/project hierarchy as Agent Runtime. **One Google connection should cover all three.** That means one Workload Identity Federation (WIF) principal, or one OAuth grant, with the union of roles: aiplatform + agentregistry + discoveryengine viewer. The ACT gallery can show two cards that share one "Google Cloud" connection record.

---

## 1. What counts as an "agent" (late 2026)

| Product / resource | API resource name | What it is | Status |
|---|---|---|---|
| **Agent Runtime** (ex-Agent Engine, ex-Reasoning Engine) | `projects/{p}/locations/{loc}/reasoningEngines/{id}` (aiplatform v1, v1beta1) | Managed hosting for ADK, LangChain, LangGraph, AG2, LlamaIndex, or custom-container agents. **This is the primary inventory object.** | GA (v1). Runtime revisions, traffic split, and agentCard are v1beta1 only. |
| Runtime revisions | `.../reasoningEngines/{id}/runtimeRevisions/{rev}` | Immutable deployed versions with `state` ACTIVE or ARCHIVED | v1beta1 (Preview) |
| Sessions / events | `.../reasoningEngines/{id}/sessions/{s}/events` | Conversation state written by the agent | GA (v1) |
| Memory Bank | `.../reasoningEngines/{id}/memories` | Long-term memories | v1; not available in 3 regions |
| **Agent Registry** | `agentregistry.googleapis.com/v1/projects/{p}/locations/{loc}/agents/{a}` (+ `mcpServers`, `endpoints`, `services`, `bindings`, `skills`, `findings`) | Catalog. Agent Runtime agents **register automatically**. Cloud Run and GKE agents need an opt-in label or flag. Manual entries are `services`. | v1 exists. GA was June–July 2026 per secondary sources (verify). |
| "Vertex agent" (personal/coworker agents) | `aiplatform v1 projects/{p}/locations/{loc}/agents/{a}` with `base_agent: antigravity-preview-05-2026` | User-owned agents with tools, a model config, an owner, and an agent identity. `list` returns **only the caller's own agents** and is empty for service accounts. | New (2026). Overlaps Gemini Enterprise, so the other researcher should cover it. |
| Monitored agents | `aiplatform v1beta1 projects/{p}/locations/{loc}/monitoredAgents` | Agent Anomaly Detection enrollment | Private Preview |
| Agent Identity | Read from `spec.effectiveIdentity`; principal `principal://agents.global.org-{ORG}.system.id.goog/resources/aiplatform/projects/{num}/locations/{loc}/reasoningEngines/{id}` | Per-agent SPIFFE identity, selected with `spec.identityType=AGENT_IDENTITY` | GA per secondary sources (Jun–Jul 2026). The primary page carries no label (verify). |
| Express-mode engines | `aiplatform /v1/reasoningEngines` (no project) | API-key "express" projects | Out of scope for banks |

ACT should treat each `reasoningEngine` as one agent and use Agent Registry as the enrichment and cross-runtime catalog. Agents on Cloud Run or GKE appear only through Agent Registry, and only if opted in.

**The existing replay adapter and fixtures do not match the real schema.** `adapters/google.py` and `fixtures/google/*` model Agent Registry `services` and `bindings`, but they use invented fields:
- **Service:** the fixture has `properties.{tools,data_scopes,value_limits,criteria}`, `identity`, and `labels` on Service. Real v1 Service has none of these. It has only `agentSpec{type,content}`, `interfaces[]`, `registryResource`, display name, description, and timestamps.
- **Binding:** the fixture has `target.agentEngine` and `authProviderBinding.serviceAccount`. The real Binding has `target.identifier` (a URN such as `urn:agent:...`, `urn:mcp:...`, or `urn:endpoint:...`), `source.identifier`, and `authProviderBinding.{authProvider,scopes,continueUri}`.

New fixtures should be re-recorded against real shapes (section 8).

## 2. Inventory APIs

### 2.1 Agent Runtime (aiplatform)
- Host: regional `https://{loc}-aiplatform.googleapis.com`. Multi-region `us` and `eu` use `aiplatform.us.rep.googleapis.com` and `aiplatform.eu.rep.googleapis.com` (verify the exact REST host for Runtime).
- **list:** `GET /v1/projects/{p}/locations/{loc}/reasoningEngines?pageSize=&pageToken=&filter=`. `filter` is AIP-160, for example `display_name="x"` or `labels.owner:*`. The response is `{reasoningEngines[], nextPageToken}`. There is no published max page size, so use 100 and follow tokens.
- **get:** `GET /v1/{name}`
- **getIamPolicy:** `POST /v1/{name}:getIamPolicy`. This shows who can invoke or manage the agent.
- **runtimeRevisions.list:** `GET /v1beta1/{name}/runtimeRevisions`. Each item has `{name, state, createTime, spec}`.
- **sessions.list:** `GET /v1/{name}/sessions?filter=&orderBy=`; **events.list:** `GET /v1/{session}/events`
- **OAuth scopes:** `reasoningEngines.list` accepts **only `cloud-platform` or `aiplatform`**. It does **not** accept `cloud-platform.read-only`, which matters for section 4.
- **Regions where Runtime runs (23):** us-east1, us-east4, us-west1, us-central1, europe-west1/2/3/4/6/8, europe-southwest1, asia-east1, asia-east2, asia-northeast1, asia-northeast3, asia-south1, asia-southeast1, asia-southeast2, australia-southeast2, me-west1, northamerica-northeast1/2, southamerica-east1. There are also multi-region endpoints `us` and `eu`, so a full scan covers 25 locations per project. Memory Bank is missing in asia-southeast2, australia-southeast2, and northamerica-northeast2.
- **Quotas** (per project per region): writes are 10/min; session reads 10,000/min; session writes 100/min; memory reads 300/min. The localized pages disagree on the entity cap (10 vs 100). I found no separate read/list quota, so expect the shared aiplatform resource-management quota (verify). Handle 429 with backoff.
- **SDK:** `google-cloud-aiplatform` with `vertexai.agent_engines.list(filter=...)`. The newer client-based SDK is `vertexai.Client(project, location).agent_engines.list()`. ACT can use plain `requests` plus `google-auth`; nothing else is needed.

Example item (v1beta1 superset; v1 lacks `agentCard`, `trafficConfig`, `agentServerMode`):
```json
{"name":"projects/bank-ai-prod/locations/us-central1/reasoningEngines/122077102645",
 "displayName":"payments-fraud-screener","description":"Scores real-time payments...",
 "createTime":"2026-10-02T09:00:00Z","updateTime":"2026-11-01T08:12:09Z","etag":"AMEw9y...",
 "labels":{"owner":"fraud-analytics","env":"prod"},
 "spec":{"agentFramework":"google-adk",
   "identityType":"SERVICE_ACCOUNT","serviceAccount":"sa-fraud@bank-ai-prod.iam.gserviceaccount.com",
   "effectiveIdentity":"sa-fraud@bank-ai-prod.iam.gserviceaccount.com",
   "classMethods":[{"name":"stream_query","api_mode":"stream","parameters":{"type":"object","properties":{"message":{"type":"string"},"user_id":{"type":"string"}}}}],
   "packageSpec":{"pickleObjectGcsUri":"gs://...","requirementsGcsUri":"gs://...","pythonVersion":"3.12"},
   "deploymentSpec":{"env":[{"name":"MODEL","value":"gemini-3.1-pro"}],"minInstances":1,"maxInstances":10,
     "agentGatewayConfig":{"clientToAgentConfig":{},"agentToAnywhereConfig":{}}},
   "agentCard":{"name":"payments-fraud-screener","skills":[{"id":"score","name":"Score payment"}]}},
 "contextSpec":{"memoryBankConfig":{"generationConfig":{"model":"projects/.../publishers/google/models/gemini-3.1-flash"}}},
 "encryptionSpec":{"kmsKeyName":"projects/.../cryptoKeys/k"}}
```
**There is no `state` or status field on a ReasoningEngine.** If the engine exists, it is deployed. Use `runtimeRevisions[].state` (ACTIVE/ARCHIVED) for revision status.

**Where to read each attribute:**

| Need | Source |
|---|---|
| Identity | `spec.identityType`, which is one of `IDENTITY_TYPE_UNSPECIFIED`, `SERVICE_ACCOUNT`, or `AGENT_IDENTITY`. `spec.effectiveIdentity` resolves to one of three forms: `service-{proj}@gcp-sa-aiplatform-re.iam.gserviceaccount.com` (the shared default service agent, which is a risk signal), a custom SA email, or the agent principal. |
| Data scopes (what the agent can reach) | Cloud Asset Inventory `searchAllIamPolicies` with `scope=organizations/{org}` and `query=policy:"<effectiveIdentity>"`. This returns every resource and role granted to the agent's identity org-wide. It is the best real "data scopes" source. Per project, `projects.getIamPolicy` is the fallback. |
| Who can invoke the agent | `reasoningEngines.getIamPolicy` |
| Tools | No first-class field on ReasoningEngine. Sources in order: (a) `spec.agentCard.skills` (v1beta1); (b) Agent Registry `agents.skills[]` and bindings to `urn:mcp:` targets; (c) `spec.sourceCodeSpec.agentConfigSource.adkConfig` when deployed from an ADK YAML config, which lists tools and the model; (d) trace spans `execute_tool <name>`, `gen_ai.tool.name`; (e) `spec.classMethods` (the invocation surface, not the tools). |
| Model ids | `deploymentSpec.env` (heuristic: keys containing `MODEL`), `contextSpec.memoryBankConfig.*.model`, the ADK config, and trace `gen_ai.request.model` (most reliable at runtime) |
| MCP / agent dependencies | Agent Registry `bindings` (source URN to target `urn:mcp:`/`urn:agent:`/`urn:endpoint:`), plus `deploymentSpec.agentGatewayConfig` (traffic is mediated by Agent Gateway) |
| Versions | `runtimeRevisions.list` (v1beta1); `trafficConfig` for split; `etag`/`updateTime` for drift |
| Created / updated | `createTime` / `updateTime` |
| Owner hints | `labels` (convention: `owner`, `team`, `cost-center`), plus the creator from Cloud Audit Logs. Admin Activity logs are always on and retained 400 days, so they need no setup. `POST https://logging.googleapis.com/v2/entries:list` with `resourceNames:["projects/{p}"]` and the filter `logName="projects/{p}/logs/cloudaudit.googleapis.com%2Factivity" AND protoPayload.methodName:"ReasoningEngineService.CreateReasoningEngine" AND protoPayload.resourceName:"reasoningEngines/{id}"` (verify the exact resourceName form). Read `protoPayload.authenticationInfo.principalEmail`. Requires `logging.logEntries.list` (`roles/logging.viewer`); Data Access logs are not needed. |
| Security posture extras | `encryptionSpec` (CMEK), `deploymentSpec.pscInterfaceConfig` (private network), `secretEnv` (names only), and `minInstances`/`maxInstances` |

### 2.2 Agent Registry (agentregistry v1)
- `GET https://agentregistry.googleapis.com/v1/projects/{p}/locations/{loc}/agents?filter=&orderBy=&pageSize=&pageToken=`; likewise `/mcpServers`, `/endpoints`, `/services`, `/bindings`, `/findings`, and `bindings:fetchAvailable`.
- **Location:** documented values include `global`, `us-central1`, and `eu`. Run `GET /v1/projects/{p}/locations` first to list the project's registry locations. A **management project** aggregates agents across the member projects in its boundary, so one call can cover many projects.
- **Scopes:** accepts `cloud-platform.read-only` and `agentregistry.read-only`. **Role:** `roles/agentregistry.viewer` (permissions `agentregistry.agents.list/get`, `services.*`, `bindings.*`, ...).
- **MCP:** `https://agentregistry.googleapis.com/mcp` (managed remote MCP).
```json
{"name":"projects/bank-ai-prod/locations/us-central1/agents/a-7f3",
 "agentId":"urn:agent:...","uid":"...","displayName":"payments-fraud-screener","description":"...","version":"1.4.0",
 "location":"us-central1","createTime":"...","updateTime":"...",
 "protocols":[{"type":"A2A_AGENT","protocolVersion":"0.3","interfaces":[{"url":"https://...","protocolBinding":"JSONRPC"}]}],
 "skills":[{"id":"score","name":"Score payment","tags":["payments"]}],
 "attributes":{"agentregistry.googleapis.com/system/Framework":{"framework":"google-adk"},
   "agentregistry.googleapis.com/system/RuntimeIdentity":{"principal":"principal://agents.global.org-123.system.id.goog/..."},
   "agentregistry.googleapis.com/system/RuntimeReference":{"uri":"//aiplatform.googleapis.com/projects/.../reasoningEngines/122077102645"}},
 "card":{"type":"A2A_AGENT_CARD","content":{}}}
```
`RuntimeReference.uri` is the join key back to the ReasoningEngine. `findings` hold vulnerability or secret-leak findings, with `severity` and `findingClass` of VULNERABILITY or SECRET, which ACT can ingest as risk evidence.

### 2.3 Org-wide shortcut: Cloud Asset Inventory
`GET https://cloudasset.googleapis.com/v1/organizations/{org}:searchAllResources?assetTypes=aiplatform.googleapis.com/ReasoningEngine&pageSize=500` (scope can also be `folders/{n}` or `projects/{id}`). **One paginated call returns every Agent Runtime agent in every project and region.** Each result carries `name`, `project`, `location`, `displayName`, `labels`, `createTime`, and `updateTime`. ReasoningEngine appears in the CAI release notes as a searchable type (verify the date; my fetch showed the 2025-11-20 entry). This needs `cloudasset.assets.searchAllResources` (`roles/cloudasset.viewer`) on the org or folder, and **only the `cloud-platform` scope** works. With user credentials it also needs a quota project (`x-goog-user-project`). CAI search is eventually consistent (minutes), so ACT should follow up with `get` on each hit.

### 2.4 Runs and traces
- **Cloud Trace:** Agent Runtime exports OTel spans when deployed with `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`, `enable_tracing=True`, or `adk deploy agent_engine --trace_to_cloud`. Prompt and response content is captured only if `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is set. Read with `GET https://cloudtrace.googleapis.com/v1/projects/{p}/traces?startTime=&endTime=&filter=&view=COMPLETE&pageSize=&pageToken=`. Scope is `trace.readonly` or `cloud-platform`; role is `roles/cloudtrace.user`. Span names and attributes follow OTel GenAI semconv (`invoke_agent`, `execute_tool`, `gen_ai.request.model`, `gen_ai.tool.name`, `gen_ai.usage.*`). The exact resource attribute linking a span to the engine id needs verifying on a live trace (likely `cloud.resource_id` or `gcp.reasoning_engine_id`). Agent Observability (OTel metrics) is a newer product; check its status.
- **Sessions:** `sessions.list` and `events.list` give per-conversation runs (`userId`, `createTime`; events carry `author`, `invocationId`, `actions`, `errorCode`). **They contain conversation content and PII**, so make them opt-in.
- **Logs:** stdout and stderr go to Cloud Logging (`resource.type="aiplatform.googleapis.com/ReasoningEngine"`, verify) and can be read with `entries:list`.
- `SourceRun` mapping: `run_id = traceId or session id`, `started_at = root span startTime or session createTime`, `status` from the span status or event `errorCode`.

### 2.5 MCP servers
These are Google-managed remote MCP servers with the same OAuth/IAM auth: `agentregistry.googleapis.com/mcp`, `cloudresourcemanager.googleapis.com/mcp`, `cloudasset.googleapis.com/mcp` (Preview), `logging.googleapis.com/mcp`, and `cloudtrace.googleapis.com/mcp`. The `aiplatform.googleapis.com/mcp/{toolset}` toolsets (generate, predict, models, endpoints, ...) **do not cover reasoningEngines**. **Verdict: use REST for the adapter.** It is deterministic, recordable for contract tests, and has typed schemas. MCP adds nothing for read-only inventory. The relevant point is that Google-managed MCP servers are themselves registered in Agent Registry, so ACT can use them to list `mcpServers` as dependencies.

## 3. Field mapping to `SourceAgent`

| SourceAgent | Agent Runtime (`reasoningEngine`) | Agent Registry fallback/enrichment |
|---|---|---|
| platform | `"google"` | |
| native_id | full `name` (`projects/../locations/../reasoningEngines/{id}`). Don't use the short id; it is not unique across regions. | `attributes.RuntimeReference.uri` (strip `//aiplatform.googleapis.com/`); else agent `name` |
| name | `displayName` | `displayName` |
| description | `description` | `description` |
| version | latest ACTIVE runtimeRevision id, else `etag` | `version` (from the A2A card) |
| owner | `labels.owner` / `labels.team`, else the audit-log creator `principalEmail` | |
| status | `"DEPLOYED"`; `"ARCHIVED"` if no ACTIVE revision; missing from list means `"DELETED"` (drift finding) | |
| last_updated | `updateTime` | `updateTime` |
| url | console deep link `https://console.cloud.google.com/vertex-ai/agents/locations/{loc}/agent-engines/{id}?project={p}` (verify path) | `protocols[0].interfaces[0].url` |
| provider | `"Google Cloud"` | |
| tools | `agentCard.skills[].name`, ADK config tools, trace `gen_ai.tool.name` (union, deduped) | `skills[].name` |
| data_scopes | CAI `searchAllIamPolicies` for `effectiveIdentity`, as `"{role} on {resource}"` | |
| systems | distinct service prefixes of the data scopes (bigquery, storage, secretmanager, ...) plus binding targets | `bindings` with `urn:endpoint:` targets |
| value_limits | none on Google. Leave empty for human entry. | |
| criteria | derived: `identityType`, default-service-agent flag, `agentGatewayConfig` present, `pscInterfaceConfig`, CMEK, and `labels.env` | `findings` severities |
| identity | `{"type": identityType→service_account/agent_identity/service_agent_default, "principal": effectiveIdentity, "distinct": not effectiveIdentity.startswith("service-")}` | `attributes.RuntimeIdentity.principal` |
| versions | `runtimeRevisions[] → {"version": id, "created": createTime, "state": state}`, else `[{"version":"1","created":createTime}]` | |
| dependencies | `{"models":[env MODEL*, memoryBank models, trace models], "mcp_servers":[binding targets urn:mcp:*]}` | bindings |
| extra | `{project, location, agentFramework, classMethods names, cmek, minInstances, maxInstances, agentGateway, registryAgent}` | |
| raw | `{"engine":…, "registryAgent":…, "revisions":[…]}` | |

## 4. Auth options ranked by user friction

The scope finding drives the ranking. A read-only OAuth scope cannot list Agent Runtime agents or call CAI. A user grant therefore needs `cloud-platform`, whose consent text reads "See, edit, configure, and delete your Google Cloud data", or `aiplatform`, which reads "See, edit, configure, and delete your ... Vertex AI data". IAM still limits the token to what the user holds, but bank security reviewers read consent text. With WIF or a service account, the scope is irrelevant and **IAM roles alone make the access read-only**.

### Option A: Google sign-in (OAuth 2.0 auth-code + PKCE, popup)
- **User steps:** click "Sign in with Google", pick the account, click Allow, then confirm projects. About 4 clicks and 0 typed fields.
- **Scopes:** `openid email https://www.googleapis.com/auth/aiplatform https://www.googleapis.com/auth/cloud-platform.read-only https://www.googleapis.com/auth/trace.readonly`. This combination lists engines (aiplatform), reads CRM, Agent Registry, and Logging (read-only), and reads Trace. It avoids full `cloud-platform` but loses CAI org-wide search, so discovery falls back to a per-project, per-region fan-out. For Gemini Enterprise, request its scope incrementally with `include_granted_scopes=true` on the same client.
- **Registration:** an OAuth web client must exist. There are two choices:
  - **Vendor client** (Domino-owned, External): `aiplatform` and `cloud-platform*` are sensitive scopes, so Google app verification is needed (weeks; privacy policy, demo video, Search Console domain). Until then there is a 100-user cap and an "unverified app" screen. Workspace admins at banks often block unconfigured third-party apps, so the customer admin must mark the app Trusted in Admin console > API controls.
  - **Per-customer client** (Internal, in the bank's own org): no verification. An admin creates it once, and ACT needs the client id and secret stored. That is a long-lived secret and a console form with a redirect URI to type, which works against the "no typing" goal.
- **Redirect constraints:** Google requires an exact-match HTTPS redirect URI with no wildcards. The published App URL `https://se-demo.domino.tech/apps/<app-id>/oauth/google/callback` is stable and fine. Dev-preview URLs change with each workspace session, so preview mode should use the published callback or a loopback/device flow. Google's device flow does **not** allow cloud-platform scopes, so device code is not an option. The popup must be opened from the top window because ACT renders inside Domino's frame. Use `postMessage` back to the opener, with `state` + PKCE stored server-side.
- **Lifetime:** access token about 1 h. The refresh token (`access_type=offline`, `prompt=consent`) is long-lived but **subject to Google Cloud session control**: the org can force reauth every 1–24 h for any app using Cloud scopes unless the app is in Trusted apps with "exempt trusted apps". In that case scheduled syncs fail with `invalid_grant` / `invalid_rapt`. A refresh token also dies when the user's password changes, the user leaves, or the token goes unused for 6 months.
- **ACT stores:** an encrypted refresh token, which is a long-lived secret, plus the granted email.
- **Verify:** `GET https://oauth2.googleapis.com/tokeninfo?access_token=` shows the scopes. Then `projects:search`, and `POST cloudresourcemanager v3 projects/{p}:testIamPermissions` with the permission list from option B.

### Option B (RECOMMENDED PRIMARY): WIF trusting the Domino OIDC issuer, set up by a one-click Cloud Shell script
Domino Apps fetch the publisher's token from `http://localhost:8899/access-token`. The issuer is `https://<host>/auth/realms/DominoRealm` and the JWKS is at `/auth/realms/DominoRealm/protocol/openid-connect/certs`. Google STS exchanges that JWT for a federated Google token. **No secret is stored, and no OAuth app needs registering or verifying.** This route is immune to session-control reauth, read-only by IAM, and org-wide through CAI.
- **Fit with Google's documented requirements:** RS256 is supported; `exp - iat <= 24h`; JWT access tokens are accepted if they carry `iss` and the issuer metadata is HTTPS-reachable. Otherwise upload the JWKS with `--jwk-json-path` (max 8 keys). `--allowed-audiences` must equal the Domino token's `aud`.
- **ACT pre-fills every value.** The ACT backend decodes the token's claims without verifying it, to read `iss`, `aud`, and `sub`, and never displays the token. ACT embeds those values, a one-time `state` nonce, and its own App URL into the command, so the user types nothing.
- **User steps (0 typed, 1 paste):**
  1. Click the Google card.
  2. Choose Recommended: "Connect without keys".
  3. Click "Copy setup command".
  4. Click "Open Cloud Shell" (`https://shell.cloud.google.com/?show=terminal`).
  5. Paste and press Enter; on first use, click Authorize in Cloud Shell.
  6. Click the "Finish in ACT" link the script prints.
  7. ACT auto-verifies and shows the project tree.

  An "Open in Cloud Shell" repo button (`?cloudshell_git_repo=...&cloudshell_tutorial=...`) is an alternative, but non-Google repos open in an ephemeral environment *without* the user's credentials and do not auto-run. The single pasted line is simpler.
- **Who runs it:** someone who can create a WIF pool in a host project and grant roles at org, folder, or project level (`roles/iam.workloadIdentityPoolAdmin`, `roles/iam.organizationRoleAdmin`, `roles/resourcemanager.organizationAdmin` or folder or project IAM admin). If the ACT user lacks these, the wizard offers "Send to my cloud admin" with a copyable link to the same command. The link carries no secret.
- **Script** (generated by ACT; idempotent; values in `<>` are filled by ACT):
```bash
set -euo pipefail
ISSUER="<https://se-demo.domino.tech/auth/realms/DominoRealm>"; AUD="<domino-aud>"; SUB="<publisher-sub>"
POOL=act-pool; PROV=act-domino; ROLE=actAgentInventoryReader; STATE="<nonce>"; ACT="<https://se-demo.domino.tech/apps/APPID>"
HOST=$(gcloud config get-value project); NUM=$(gcloud projects describe "$HOST" --format='value(projectNumber)')
ORG=$(gcloud projects get-ancestors "$HOST" --format='value(id,type)' | awk '$2=="organization"{print $1}')
gcloud services enable iam.googleapis.com sts.googleapis.com iamcredentials.googleapis.com cloudresourcemanager.googleapis.com cloudasset.googleapis.com --project "$HOST"
gcloud iam workload-identity-pools describe $POOL --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create $POOL --location=global --display-name="ACT (Domino) read-only"
gcloud iam workload-identity-pools providers describe $PROV --location=global --workload-identity-pool=$POOL >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc $PROV --location=global --workload-identity-pool=$POOL \
    --issuer-uri="$ISSUER" --allowed-audiences="$AUD" \
    --attribute-mapping="google.subject=assertion.sub" --attribute-condition="assertion.sub=='$SUB'"
PERMS=resourcemanager.projects.get,resourcemanager.folders.get,serviceusage.services.list,\
aiplatform.reasoningEngines.list,aiplatform.reasoningEngines.get,aiplatform.reasoningEngines.getIamPolicy,\
aiplatform.reasoningEngineRuntimeRevisions.list,aiplatform.reasoningEngineRuntimeRevisions.get,\
agentregistry.agents.list,agentregistry.agents.get,agentregistry.services.list,agentregistry.services.get,\
agentregistry.bindings.list,agentregistry.bindings.get,agentregistry.mcpServers.list,agentregistry.mcpServers.get,\
cloudasset.assets.searchAllResources,cloudasset.assets.searchAllIamPolicies,logging.logEntries.list,\
cloudtrace.traces.list,cloudtrace.traces.get
gcloud iam roles describe $ROLE --organization="$ORG" >/dev/null 2>&1 || \
  gcloud iam roles create $ROLE --organization="$ORG" --title="ACT agent inventory reader" --permissions="$PERMS"
M="principal://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/$POOL/subject/$SUB"
gcloud organizations add-iam-policy-binding "$ORG" --member="$M" --role="organizations/$ORG/roles/$ROLE" --condition=None
gcloud projects add-iam-policy-binding "$HOST" --member="$M" --role=roles/serviceusage.serviceUsageConsumer --condition=None
CFG=$(printf '{"org":"%s","num":"%s","pool":"%s","prov":"%s","host":"%s","state":"%s"}' "$ORG" "$NUM" $POOL $PROV "$HOST" "$STATE" | base64 -w0)
echo "Done. Finish in ACT: $ACT/connect/google/finish?cfg=$CFG"
```
  - **No organization, or the user is not an org admin:** a project-mode variant uses `gcloud iam roles create --project` and binds on each project from `gcloud projects list` that the user can administer.
  - **Gemini Enterprise:** add `discoveryengine.*.get/list` permissions to the same role.
  - **Sessions and traces with content:** opt-in only; append `aiplatform.sessions.list` and `aiplatform.sessionEvents.list`.
  - **Deliberately excluded:** `aiplatform.reasoningEngines.query`. **`roles/aiplatform.viewer` includes `reasoningEngines.query`, which can invoke agents**, and that breaks ACT's "only reads" promise. This is why the script uses a custom role.
- **Some permissions may not be custom-role-eligible.** The script should catch those, drop them, and print which ones were dropped (verify the list). The fallback is predefined roles: `roles/browser`, `roles/agentregistry.viewer`, `roles/cloudasset.viewer`, `roles/logging.viewer`, `roles/cloudtrace.user`, and `roles/aiplatform.viewer`, the last with a warning about `query`.
- **Runtime:** use `google.auth.identity_pool.Credentials` with a **URL-sourced** credential: `credential_source={"url":"http://localhost:8899/access-token","format":{"type":"text"}}`. The audience is `//iam.googleapis.com/projects/{num}/locations/global/workloadIdentityPools/act-pool/providers/act-domino`, and `subject_token_type=urn:ietf:params:oauth:token-type:jwt`. There is no SA impersonation, because the docs list no federation limitations for Agent Platform, Trace, or Logging. Send `x-goog-user-project: {host}` for CAI. STS returns a roughly 1 h token. ACT re-exchanges on expiry, and each exchange fetches a fresh Domino token (Keycloak tokens are short-lived; verify the TTL).
- **ACT stores:** `{org, num, pool, prov, host}`. These are non-secret, so no secret store is needed.
- **Wizard verification** after "Finish": ACT checks `state`, then performs the STS exchange (proving the trust), then `projects:search`, then `organizations/{org}:searchAllResources?assetTypes=aiplatform.googleapis.com/ReasoningEngine&pageSize=1`, then `projects/{p}:testIamPermissions` with `PERMS` on a sample project. Each missing permission becomes a row in the checklist.
- **Caveats:**
  - The trust binds to the App **publisher's** `sub`. If someone else republishes the App, or a scheduled Domino Job runs as another user, the sub changes and the token is denied. The fix is to rerun the script with the extra sub, or to map a Keycloak group or claim (`attribute.act=assertion.<claim>`) and grant to a `principalSet`.
  - Any run that can mint that user's Domino token can reach this read-only access. Combined with the custom role, the impact is low.
  - On-prem or private Domino hosts whose JWKS Google cannot reach need `--jwk-json-path`. Keycloak key rotation then breaks the trust until the JWKS is re-uploaded.
  - Org policy `constraints/iam.workloadIdentityPoolProviders` may allowlist issuers, and an admin must add the Domino issuer.

### Option C: service-account key JSON (last resort)
- **User steps:** create an SA, grant the role, create a key, and upload the file. ACT can generate a Cloud Shell script that does all of this and prints the JSON, and the user uploads it with a file picker (0 typed).
- **Blocked by default:** `constraints/iam.disableServiceAccountKeyCreation` is enforced by default for organizations created since 2024, and most banks enforce it.
- **Risks:** the key is a long-lived secret, encrypted at rest in ACT. The wizard should warn about key age and suggest 90-day rotation.
- **Verify:** the same checks as option B.

**Recommendation.**
- **Primary: option B, WIF with the Domino issuer, provisioned by Cloud Shell.** It is secretless, needs no vendor OAuth verification and no per-customer OAuth client, is read-only by IAM, covers the whole org in one CAI call, and survives reauth policies. Gemini Enterprise and Agent Registry are covered by the same principal.
- **Fallback: option A, Google sign-in.** Use it once Domino ships a verified OAuth client, or where the bank pre-registers an Internal client. It is the right path for an individual who cannot run admin setup, and the only path for the user-owned aiplatform `agents` resource. Keep option C behind "Other options".

## 5. Auto-discovery of scopes (no typing)
1. **Identity:** WIF uses the `org` from the connection. OAuth calls `GET https://cloudresourcemanager.googleapis.com/v3/projects:search?query=state:ACTIVE` and derives the orgs from `parent`.
2. **Agents:**
   - Org-wide: one CAI call per org, `searchAllResources?assetTypes=aiplatform.googleapis.com/ReasoningEngine`, gives `{project, location}` pairs with counts per project.
   - Without CAI (OAuth mode or missing role): for each project, call `serviceusage v1 projects/{p}/services?filter=state:ENABLED`. If aiplatform is not enabled, the project has 0 agents and the call is skipped. Otherwise fan out `reasoningEngines.list` over the 25 locations with concurrency about 8 per project and backoff on 429.
3. **Registry:** `agentregistry v1 projects/{p}/locations` lists the project's registry locations, then `agents.list` runs per location. Prefer a management project when one exists, because one location query aggregates its members.
4. **UI:** an org > folder > project tree with an agent-count badge. Projects with agents start pre-checked and empty ones are greyed out. A "Select all with agents" option is the default, and new projects are auto-included on scheduled sync.

## 6. Wizard steps
1. **Choose:** the Google Cloud card. A pill shows "Also covers Gemini Enterprise and Agent Registry".
2. **Connect:**
   - Default "Connect without keys" (B): copy command, open Cloud Shell, then click the finish link.
   - Alternative "Sign in with Google" (A), shown only when an OAuth client is configured.
   - "Upload key" (C) sits under Other options.
3. **Check:** a live checklist shows the STS exchange, organization visible, Agent Runtime readable, Registry readable, audit logs readable, and traces readable. Each item is ok or shows a one-line fix.
4. **Scopes:** the auto-discovered tree from section 5, then Next.
5. **Discover:** a progress bar per project. Staged rows are marked new, changed, or unchanged.
6. **Import:** pick agents (all checked), review the suggested tiers, then Import. A schedule toggle offers "Sync daily", which runs as a scheduled Domino Job under the same publisher identity.

**Common failures.** Google errors arrive as `{"error":{"code","status","message","details":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason","metadata":{...}}]}}`. Key on `details[].reason`.

| HTTP / status | reason (ErrorInfo) or error | Plain-language fix |
|---|---|---|
| 403 PERMISSION_DENIED | `SERVICE_DISABLED` (metadata `service`, `activationUrl`) | "The Agent Platform API is off in project X, so it has no agents." Skip silently. For CAI or STS on the host project, show the `activationUrl` button. |
| 403 PERMISSION_DENIED | `IAM_PERMISSION_DENIED` (metadata `permission`) | "Your connection can't do `<permission>` in X." Show "Re-run setup", which offers the same command. |
| 403 PERMISSION_DENIED | `USER_PROJECT_DENIED` / quota project missing | "Pick a billing/quota project for read calls." ACT sets the host project automatically. If that still fails, the grant of `serviceusage.serviceUsageConsumer` failed. |
| 403 PERMISSION_DENIED | `SECURITY_POLICY_VIOLATED` (VPC Service Controls) | "A VPC-SC perimeter blocks reads from outside." An admin must add an ingress rule for the ACT principal. |
| 403 PERMISSION_DENIED | `ACCESS_TOKEN_SCOPE_INSUFFICIENT` | OAuth only: "Sign in again and allow access." For example, `cloud-platform.read-only` was used for reasoningEngines. |
| 401 UNAUTHENTICATED | `ACCESS_TOKEN_EXPIRED` / `CREDENTIALS_MISSING` | Re-exchange or refresh automatically. Show a message only on repeat. |
| 400 (STS) | `invalid_grant`: "The audience in ID Token ... does not match the expected audience" | "The Domino token audience changed." Re-run setup, and ACT pre-fills the new `aud`. |
| 400/403 (STS) | `invalid_grant`: "The given credential is rejected by the attribute condition" | "This App's publisher changed." Re-run setup to trust the new publisher. |
| 400 (STS) | `invalid_grant`: issuer or JWKS unreachable, or signature invalid | "Google can't reach your Domino sign-in keys." Use the private-issuer variant, which uploads the JWKS. |
| 400 (STS) | `invalid_target`: pool or provider not found or disabled | "Setup was removed." Re-run setup. |
| 400 FAILED_PRECONDITION (setup) | org policy `iam.workloadIdentityPoolProviders` / `iam.disableServiceAccountKeyCreation` | An admin must allow the Domino issuer, or use option A. |
| 429 RESOURCE_EXHAUSTED | `RATE_LIMIT_EXCEEDED` / `RESOURCE_EXHAUSTED` | Automatic backoff, honoring `RetryInfo`. Show "slowing down" only. |
| OAuth refresh | `invalid_grant` + `invalid_rapt` / "reauth related error" | "Your org requires Google Cloud re-sign-in every N hours." Sign in again, or ask an admin to trust ACT, or switch to option B. |
| OAuth consent | `admin_policy_enforced` / `access_denied` / `org_internal` | "Your Google admin blocks this app." Admin must trust the client id, or use option B. |
| 404 NOT_FOUND | on get after list or CAI | The agent was deleted between calls. Mark it missing and raise a drift finding. |

## 7. Intake of one agent (extra calls)
1. `GET v1/{name}`, and for the v1beta1 extras `GET v1beta1/{name}`, which adds `agentCard`, `trafficConfig`, and `agentServerMode`.
2. `GET v1beta1/{name}/runtimeRevisions` gives versions and state.
3. `POST v1/{name}:getIamPolicy` shows who can invoke the agent.
4. CAI `searchAllIamPolicies?scope=organizations/{org}&query=policy:"{effectiveIdentity}"` gives data scopes and systems. For the default service agent, flag that the agent shares an identity with every default-identity agent in the project.
5. Agent Registry `agents.list?filter=` on the RuntimeReference. If the filter is unsupported, list and match locally. Then `bindings.list` for this agent's URN, which gives MCP and agent dependencies, and `findings.list?filter=resource=...`.
6. `entries:list` on the Admin Activity audit log for `CreateReasoningEngine` and `UpdateReasoningEngine` gives the creator, last changer, and change history.
7. Optional: `cloudtrace v1 traces.list` for the last 7 days with a filter on the engine id, which gives observed tools, models, and run counts. Sessions are read only if opted in.

## 8. Recorded-HTTP fixtures for contract tests
Store each fixture as a request (method, URL, query, relevant headers) plus a response (status, headers, body).
- `sts_exchange_ok.json`, `sts_audience_mismatch.json`, `sts_attribute_condition_denied.json`, `sts_jwks_unreachable.json`
- `crm_projects_search_page1.json` + `_page2.json` (with `nextPageToken`), `crm_test_iam_permissions_partial.json`
- `serviceusage_services_enabled.json`, `serviceusage_aiplatform_absent.json`
- `cai_search_reasoning_engines_page1.json` / `_page2.json`, `cai_permission_denied.json` (IAM_PERMISSION_DENIED, `cloudasset.assets.searchAllResources`), `cai_user_project_denied.json`
- `cai_search_iam_policies_for_sa.json`, `cai_search_iam_policies_default_service_agent.json`
- `re_list_uscentral1_page1.json` / `_page2.json`, `re_list_empty_europe_west1.json`, `re_list_service_disabled.json` (SERVICE_DISABLED with activationUrl), `re_list_permission_denied.json`, `re_list_429.json` (with RetryInfo) followed by `re_list_retry_ok.json`, `re_list_vpcsc_violation.json`
- `re_get_adk_service_account.json`, `re_get_agent_identity.json`, `re_get_default_service_agent.json`, `re_get_container_spec.json`, `re_get_404.json`
- `re_get_iam_policy.json`, `re_runtime_revisions.json` (ACTIVE + ARCHIVED)
- `ar_locations.json`, `ar_agents_list.json` (with RuntimeReference/RuntimeIdentity), `ar_bindings_list.json` (urn:mcp target), `ar_services_list.json` (manual Cloud Run agent), `ar_findings_list.json`
- `logging_entries_create_audit.json`, `logging_entries_empty.json`
- `trace_list_adk_spans.json` (execute_tool + gen_ai.request.model), `trace_list_page2.json`
- `oauth_token_ok.json`, `oauth_refresh_invalid_rapt.json`, `oauth_insufficient_scope.json`

## 9. Risks and unknowns
1. **The Domino token as a WIF subject.** Its `aud` value, TTL, whether Google accepts this Keycloak access token (`iss` matches the discovery doc, RS256), and whether se-demo's JWKS is publicly reachable all need a live test. The `sub` is the App publisher, so a republish or a Job run by another user breaks the trust unless a group-claim `principalSet` is used.
2. **The read-only story depends on IAM, not OAuth scope.** `reasoningEngines.list` and CAI require `cloud-platform`/`aiplatform`, so OAuth consent text says "edit/delete". `roles/aiplatform.viewer` includes `reasoningEngines.query`. Use the custom role.
3. **No first-class tools or model fields** on ReasoningEngine. Tools and models come from agent cards, ADK config, env heuristics, and traces. Traces exist only if telemetry was enabled at deploy, so many agents will show empty tools until a human fills them in.
4. **No status field.** Revisions and traffic config are v1beta1 (Preview), so the field shape may change.
5. **Fixture drift.** The current `fixtures/google/*` shapes are not the real Agent Registry v1 schema, so the replay adapter will mislead contract tests (see section 1).
6. **Product churn.** The Vertex AI to Agent Platform rename (April 2026), Agent Engine to Agent Runtime, new `aiplatform v1 agents`, and Agent Registry GA timing all come from secondary sources. Re-check the release notes before build.
7. **Discovery cost.** The fallback scan is 25 locations × N projects. The CAI eventual-consistency lag (minutes) means a just-deployed agent may be missed until the next sync.
8. **Org policy and VPC-SC** can block WIF providers or external reads. Session control can break OAuth refresh.
9. **PII in sessions and traces.** Keep these opt-in, and store only metadata such as counts, tool names, and model ids.
10. **Agent Identity lifecycle.** Deleting an agent leaves inactive IAM bindings, and a replaced agent gets a new principal. ACT should flag orphaned grants when the owning engine is gone.

### Sources (accessed 2026-11-12)
- Discovery docs (live): https://aiplatform.googleapis.com/$discovery/rest?version=v1 and ?version=v1beta1; https://agentregistry.googleapis.com/$discovery/rest?version=v1; https://cloudasset.googleapis.com/$discovery/rest?version=v1; https://logging.googleapis.com/$discovery/rest?version=v2; https://cloudtrace.googleapis.com/$discovery/rest?version=v1; https://cloudresourcemanager.googleapis.com/$discovery/rest?version=v3; https://serviceusage.googleapis.com/$discovery/rest?version=v1
- reasoningEngines REST: https://docs.cloud.google.com/vertex-ai/docs/reference/rest/v1/projects.locations.reasoningEngines
- Agent locations: https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/agent-locations?hl=en
- Agent Platform IAM roles: https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/general/access-control
- Agent identity: https://docs.cloud.google.com/agent-builder/agent-engine/agent-identity?hl=en ; https://docs.cloud.google.com/iam/docs/agent-identity-overview
- Agent Registry: https://docs.cloud.google.com/agent-registry/component-lifecycle-and-discovery ; https://docs.cloud.google.com/agent-registry/reference/rest ; https://docs.cloud.google.com/agent-registry/roles-permissions ; https://docs.cloud.google.com/agent-registry/manage-agents
- Tracing: https://docs.cloud.google.com/agent-builder/agent-engine/manage/tracing
- Quotas: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/quotas (localized versions disagree)
- Managed MCP servers: https://docs.cloud.google.com/mcp/supported-products
- WIF with OIDC: https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-providers ; limitations: https://docs.cloud.google.com/iam/docs/federated-identity-supported-services
- CRM projects.search: https://docs.cloud.google.com/resource-manager/reference/rest/v3/projects/search
- CAI release notes (ReasoningEngine type): https://docs.cloud.google.com/asset-inventory/docs/release-notes
- Cloud Shell button: https://docs.cloud.google.com/shell/docs/open-in-cloud-shell
- OAuth unverified apps: https://support.google.com/cloud/answer/7454865 ; session control: https://docs.cloud.google.com/access-context-manager/docs/session-controls-for-reauthentication and https://knowledge.workspace.google.com/admin/security/set-session-length-for-google-cloud-services
- Domino App identity / token service: https://docs.domino.ai/cloud/platform-capabilities/core-concepts/apps/identity
- Rebrand (secondary): https://www.hpcwire.com/aiwire/2026/04/23/google-unveils-gemini-enterprise-agent-platform/ ; https://dev.to/gde/six-months-after-cloud-next-26-a-builders-tour-of-the-gemini-enterprise-agent-platform-3hib
