# Provider research: Databricks agents

Researched 2026-11-12. Research only, no app code. Sources are in section 9, all accessed 2026-11-12.
Where a fact comes from search snippets or memory rather than a fetched primary page, it is marked **(verify)**.

**Bottom line.**
- **Auth:** the primary path is a **federated service principal (SP)**, which stores no secret. The SP's federation policy trusts this Domino deployment's Keycloak issuer, pinned to one Domino service account's `sub` and to `aud=domino-platform`. ACT exchanges its 5-minute Domino JWT for a Databricks token at `/oidc/v1/token`. An account admin sets this up once by running one generated Terraform or CLI script. The fallback is **OAuth U2M** (a public client with PKCE) through a custom OAuth app that ACT registers in the account.
- **Inventory:** there is no single "list agents" API. ACT unions these sources:
  - serving endpoints (agent endpoints)
  - Apps
  - the Agent Bricks resources (`/api/2.1/knowledge-assistants` and `/api/2.1/supervisor-agents`, both Beta)
  - UC registered models, functions and grants
  - UC **agent services** (Beta)
- **The fixture's API is real but its shape is invented.** `/api/2.1/unity-catalog/agent-services` exists (Beta, docs dated 2026-09-11). It registers **external** agents behind a UC connection, and its fields are `name`, `agent_service_type`, `comment`, `created_by` and `config{source_connection, base_path, system_prompt}`. `owner` and `full_name` come back `null`. Nothing like `agent_service_id`, `identity`, `properties.tools`, `data_scopes` or `criteria` exists (section 9, risk 1).
- **No typed URL:** a fully zero-typing flow is possible only on Azure, by signing in with Entra and listing the workspaces through ARM. On AWS and GCP, ACT needs the **account ID**. The user gets it by pasting a short non-secret connection code that the provisioning script prints, or by pasting any Databricks URL from their browser. ACT derives the rest.

---

## 1. What counts as an "agent" on Databricks (Nov 2026)

| Product / resource | How it shows up in the API | Agent for ACT? | Status |
|---|---|---|---|
| **Custom agent on Model Serving** (Mosaic AI Agent Framework, `agents.deploy()`, MLflow `ResponsesAgent`/`ChatAgent`) | A serving endpoint whose served entity is a UC registered model. Its `task` is `agent/v1/responses` or `agent/v2/chat` and so on **(verify the exact values)**. | **Yes, primary for existing estates.** | GA |
| **Custom agent on Databricks Apps** (Agent Bricks CLI standalone agent, or the AppKit `agents` plugin) | An App (`/api/2.0/apps`) with its own SP. Invoked at `<app-url>/api/invocations`. | **Yes.** This is now the recommended home for new code agents. | Apps GA; Agent Bricks CLI and AppKit agents are new in 2026 **(verify the GA/Beta label)** |
| **Agent Bricks: Knowledge Assistant** | `/api/2.1/knowledge-assistants`. Each one is backed by a serving endpoint (`endpoint_name`) and an MLflow experiment. | **Yes** | API is Beta; the product is marked "legacy" for new builds |
| **Agent Bricks: Supervisor Agent** (multi-agent) | `/api/2.1/supervisor-agents` plus `/tools` subresources (genie_space, knowledge_assistant, uc_function, uc_connection, app, vector_search_index, table, skill, and others) | **Yes.** It has the richest tool list. | API is Beta. The older **Supervisor API reached end of life on 2026-09-30.** |
| **Genie Agent / Genie spaces** | `/api/2.0/genie/spaces` **(verify)** | Optional (a conversational analytics agent). Also a tool for other agents. | Genie GA; "Genie Agent" naming is new in 2026 |
| **UC agent services** | `/api/2.1/unity-catalog/agent-services`, securable type `AGENT_SERVICE` | **Yes. A registry of *external* agents** (host and credentials sit in a UC connection). | **Beta.** An account admin enables the "Unity Gateway" beta on the account Previews page. |
| UC registered model (agent model versions) | `/api/2.1/unity-catalog/models` | Version history for endpoint agents. Not a separate agent. | GA |
| UC functions | `/api/2.1/unity-catalog/functions` | **Tools** | GA |
| Managed MCP servers | `/api/2.0/mcp/{genie,ai-search,functions,sql}/…` | **Tool surfaces.** Map them to `dependencies.mcp_servers`. | Public Preview (workspace admin enables it) |
| Unity Gateway MCP services (`system.ai.*`, registered external MCPs, custom MCP on Apps) | `/ai-gateway/mcp-services/<name>` | Tool surfaces | Gateway GA; individual MCPs range from GA to Beta |
| Unity (AI) Gateway on endpoints | the `ai_gateway` block on the endpoint (guardrails, rate limits, inference tables, usage tracking) | Guardrail and runtime evidence. Map it to `criteria` and `extra`. | GA. Contextual Service Policies are Beta. |
| Skills in UC | Announced at DAIS 2026 | Map to tools (later) | Beta **(verify the API)** |

Product guidance (developers.databricks.com): new low-code agents should be **Genie Agent**, and new code agents should use the **Agent Bricks CLI on Apps**. Knowledge Assistant and Supervisor Agent are labelled legacy, but banks will have all of these side by side in 2026, so ACT should list them all.

---

## 2. Inventory APIs

All workspace APIs live on `https://<workspace-host>`; account APIs live on the account host (see section 5). Every call uses `Authorization: Bearer <token>`. Pagination is `page_token`/`page_size` (or `max_results`) in, `next_page_token` out. An empty or absent token ends the list.
Throttling returns HTTP 429 `REQUEST_LIMIT_EXCEEDED`, sometimes with `Retry-After`. Back off exponentially with jitter.

### 2.1 Serving endpoints (agent endpoints)
- `GET /api/2.0/serving-endpoints` returns `endpoints[]`. It is not paginated **(verify)** and lists only the endpoints the caller can view.
- `GET /api/2.0/serving-endpoints/{name}` returns `InferenceEndpointDetailed`.
- `GET /api/2.0/permissions/serving-endpoints/{endpoint_id}` returns the ACL.
- The fields that matter are below. Timestamps are epoch milliseconds.
  - `name`, `id`, `creator` (email), `creation_timestamp`, `last_updated_timestamp`, `description`, `tags[{key,value}]`, `task`
  - `state.ready` (`READY|NOT_READY`) and `state.config_update` (`NOT_UPDATING|IN_PROGRESS|UPDATE_FAILED|UPDATE_CANCELED`)
  - `config.config_version` and `config.traffic_config.routes[]`
  - `config.served_entities[]`, each with `name`, `entity_name` (UC `cat.sch.model`), `entity_version`, `environment_vars`, `external_model`, `foundation_model`, `workload_size`, `scale_to_zero_enabled`, `state.deployment`, `creator` and `creation_timestamp`
  - `ai_gateway`: `{inference_table_config{catalog_name,schema_name,table_name_prefix,enabled}, usage_tracking_config, rate_limits[], guardrails{input,output}}`
  - `permission_level`, `budget_policy_id`, `route_optimized`, `endpoint_url`
- The docs say **"agent endpoints currently only support inference tables"** from the AI Gateway features.

```json
{"name":"agents_bank_prod-agents-liquidity_forecast","id":"7d1c…","creator":"jane@bank.com",
 "creation_timestamp":1759395600000,"last_updated_timestamp":1793603733000,"task":"agent/v1/responses",
 "state":{"ready":"READY","config_update":"NOT_UPDATING"},"tags":[{"key":"owner","value":"treasury"}],
 "config":{"config_version":4,"served_entities":[{"name":"liquidity_forecast-3","entity_name":"bank_prod.agents.liquidity_forecast",
   "entity_version":"3","environment_vars":{"OPENAI_KEY":"{{secrets/treasury/openai}}"},"scale_to_zero_enabled":true}]},
 "ai_gateway":{"inference_table_config":{"catalog_name":"bank_prod","schema_name":"agents","table_name_prefix":"liquidity","enabled":true}},
 "permission_level":"CAN_VIEW"}
```

**Telling agent endpoints from plain model endpoints.** Apply these tests in order, and record which test fired in `extra.agent_signal`:
1. `task` starts with `agent/`.
2. The endpoint's `name` is an endpoint name that some Knowledge Assistant or Supervisor Agent reports in its `endpoint_name`.
3. The name starts with `agents_` (the `agents.deploy()` default).
4. The served entity is a UC model whose version's MLmodel flavor is `ResponsesAgent`/`ChatAgent`/`ChatModel`.

Endpoints with `foundation_model` or `external_model` only, or with `task` `llm/v1/*`, are **models, not agents**. They become `dependencies.models` when an agent calls them.

### 2.2 Databricks Apps
`GET /api/2.0/apps?page_size=&page_token=` returns `apps[]`. Fetch one with `GET /api/2.0/apps/{name}`, its deployments with `GET /api/2.0/apps/{name}/deployments`, and its ACL with `GET /api/2.0/permissions/apps/{name}`.
Fields: `name`, `id`, `description`, `url`, `app_status{state,message}`, `compute_status{state}`, `creator`, `create_time`, `update_time`, `updater`, **`service_principal_id` / `service_principal_client_id` / `service_principal_name`** (the agent's distinct identity), **`user_api_scopes` / `effective_user_api_scopes`** (on-behalf-of-user (OBO) scopes), `resources[]` (typed `serving_endpoint`, `sql_warehouse`, `secret`, `uc_securable`, `genie_space`, `database`, `job` with a permission), `active_deployment{deployment_id,source_code_path}`, `budget_policy_id`.

```json
{"apps":[{"name":"kyc-review-agent","url":"https://kyc-review-agent-123.aws.databricksapps.com","app_status":{"state":"RUNNING"},
 "creator":"ops@bank.com","create_time":"2026-08-02T10:11:00Z","service_principal_client_id":"9a1e…","service_principal_name":"app-kyc-review-agent",
 "user_api_scopes":["serving.serving-endpoints","sql"],"resources":[{"name":"llm","serving_endpoint":{"name":"databricks-claude-sonnet","permission":"CAN_QUERY"}},
 {"name":"kyc","uc_securable":{"securable_full_name":"bank_prod.kyc.cases","securable_type":"TABLE","permission":"SELECT"}}]}],"next_page_token":null}
```
- **Telling agent apps from other apps.** No flag exists **(verify)**. Use these heuristics:
  - A `serving_endpoint` resource points at an LLM or agent endpoint.
  - The app is used as a `tool` of type `app` in a Supervisor Agent.
  - The `GET <app-url>/.well-known/agent.json` or `/api/invocations` route answers.
  - A tag or description says "agent".
- ACT should show non-agent apps as "possible agents" that the user can include.

### 2.3 Agent Bricks (Beta APIs)
- `GET /api/2.1/knowledge-assistants?page_size≤100&page_token=` returns `knowledge_assistants[]` with `{name,id,display_name,description,instructions,creator,create_time,endpoint_name,experiment_id,state,error_info}` plus `next_page_token`. API scope: `knowledge-assistants`.
- `GET /api/2.1/supervisor-agents?page_size≤100` returns `supervisor_agents[]` with `{name:"supervisor-agents/<uuid>",supervisor_agent_id,display_name,description,instructions,creator,create_time,endpoint_name,experiment_id}`.
- `GET /api/2.1/supervisor-agents/{id}/tools` returns `tools[]`. Each tool has a type field, one of `genie_space|knowledge_assistant|uc_function|uc_connection|app|volume|dashboard|table|vector_search_index|catalog|schema|supervisor_agent|databricks_web_search|skill`, plus `description`.
- `GET /api/2.0/permissions/supervisor-agents/{id}` returns the ACL **(verify the path; the SDK has `get_permissions`)**.
```json
{"supervisor_agents":[{"name":"supervisor-agents/3f2a…","supervisor_agent_id":"3f2a…","display_name":"payments-ops-supervisor",
 "description":"Routes payment exception questions","creator":"ops@bank.com","create_time":"2026-05-01T09:00:00Z",
 "endpoint_name":"mas-3f2a…-endpoint","experiment_id":"412…"}],"next_page_token":""}
```

### 2.4 UC agent services (Beta): **the fixture's API, corrected**

| Call | Endpoint |
|---|---|
| List in one schema | `GET /api/2.1/unity-catalog/agent-services?parent=schemas/{cat}.{sch}` |
| List the whole metastore | `GET /api/2.1/unity-catalog/agent-services` |
| Get one | `GET /api/2.1/unity-catalog/agent-services/{cat}.{sch}.{name}` |
| Grants | `GET /api/2.1/unity-catalog/permissions/AGENT_SERVICE/{full_name}` |

The documented object (create response) is:
```json
{"name":"agent-services/main.default.support_agent","agent_service_type":"AGENT_SERVICE_TYPE_EXTERNAL",
 "created_by":"you@company.com","comment":"Support agent for the customer team",
 "config":{"source_connection":{"name":"connections/main.default.my_agent_connection"},"base_path":"/v1/chat",
           "system_prompt":"You are a helpful support assistant."}}
```
- The list wrapper key and pagination are **not documented**. It is probably `agent_services[]` plus `next_page_token`, but this must be recorded live.
- Known limitations:
  - `full_name` and `owner` come back `null`, so ACT must parse `name` and take ownership from `created_by` or from the grants.
  - `BROWSE` is not supported. The caller needs `EXECUTE`, `READ METADATA` or `MANAGE` on each service to see it.
  - There is no Global Search, and the service cannot be invoked through UC yet.
- Follow `config.source_connection` with `GET /api/2.1/unity-catalog/connections/{name}` to get the host. The connection's `options` hide credentials **(verify)**.
- If the preview is off, the API probably returns **404 `FEATURE_DISABLED`/`ENDPOINT_NOT_FOUND` (record it)**. Treat that as "not enabled", not as an error.

### 2.5 Unity Catalog: versions, tools, data scopes
- **Scopes:** `GET /api/2.1/unity-catalog/metastore_summary` returns the metastore ID, region and cloud. `GET /api/2.1/unity-catalog/catalogs` returns `catalogs[]`, and `GET /api/2.1/unity-catalog/schemas?catalog_name=` returns the schemas. Both take `max_results` and `page_token`.
- **Models:** `GET /api/2.1/unity-catalog/models?catalog_name=&schema_name=&max_results=&page_token=` returns `registered_models[]` with `{full_name,owner,comment,created_at,created_by,updated_at,updated_by,aliases[]}`.
- **Versions:** `GET /api/2.1/unity-catalog/models/{full_name}/versions` returns `model_versions[]` with `{version,status(READY|PENDING_REGISTRATION|FAILED_REGISTRATION),run_id,source,created_at,created_by,comment,aliases, model_version_dependencies.dependencies[] ({table:{table_full_name}}|{function:{function_full_name}}|{connection:…})}` **(verify the dependency shape)**. These dependencies are the agent's declared resources, which makes them the best source for **tools and data scopes** without downloading artifacts.
- **Functions:** `GET /api/2.1/unity-catalog/functions?catalog_name=&schema_name=` returns `functions[]` with `{full_name,owner,comment,input_params,data_type,routine_body,routine_definition,external_language,created_at,created_by,updated_at}`.
- **Grants (data scopes, who can use the agent):**
  - `GET /api/2.1/unity-catalog/permissions/{securable_type}/{full_name}` returns `privilege_assignments[{principal,privileges[]}]`.
  - `GET /api/2.1/unity-catalog/effective-permissions/{type}/{full_name}?principal=<agent SP>` returns what the agent's identity can actually read or write.
  - For each agent identity (an app SP, or the endpoint creator for automatic auth passthrough), call effective-permissions on the tables and functions it depends on. That yields `data_scopes` with read/write flags.
- **Vector search / AI Search indexes:** `GET /api/2.0/vector-search/indexes?endpoint_name=` and `GET /api/2.0/vector-search/indexes/{name}` give the source table, which becomes a data scope.
- **Model artifacts (optional, heavier):** the MLmodel file holds `resources` (`DatabricksServingEndpoint`, `DatabricksFunction`, `DatabricksVectorSearchIndex`, `DatabricksGenieSpace`, `DatabricksTable`, `DatabricksUCConnection`, `DatabricksSQLWarehouse`) and **`auth_policy`** (`system_auth_policy` versus `user_auth_policy.api_scopes`, which means OBO). Reading it means downloading `models:/cat.sch.model/<v>/MLmodel` with the MLflow client, which uses temporary model-version credentials. Do this in the intake step only.

### 2.6 Identity
| Agent kind | `identity` |
|---|---|
| App | `{type:"service_principal", principal: service_principal_name, client_id, distinct:true}`. Add `obo_scopes: user_api_scopes` when that list is non-empty. |
| Endpoint, automatic auth passthrough | `{type:"system_sp_passthrough", principal: <endpoint creator>, distinct:false}`. Databricks mints a short-lived SP credential that is scoped to the declared resources and authorised by the creator's rights. |
| Endpoint with `user_auth_policy` | `{type:"on_behalf_of_user", scopes:[…]}` |
| Endpoint with PAT/M2M env vars | `{type:"secret_in_env", principal:"unknown", distinct:false}`. **Red flag.** |
| Knowledge Assistant / Supervisor | Runs as the system SP (passthrough) with its creator's grants **(verify)** |

### 2.7 Traces
- **MLflow 3 traces** (GA):
  - `POST /api/3.0/mlflow/traces/search` takes `{locations:[{type:"MLFLOW_EXPERIMENT",mlflow_experiment:{experiment_id}}] | UC catalog.schema locations, filter, max_results, order_by, page_token}`. It returns `traces[]` TraceInfo with `{trace_id, trace_location, request_time, execution_duration, state (OK|ERROR|IN_PROGRESS), request_preview, response_preview, trace_metadata, tags}` **(verify the exact REST body; the SDK's `search_traces(locations=…)` is documented)**.
  - `GET /api/3.0/mlflow/traces/{trace_id}` returns one TraceInfo. Spans come from `/api/3.0/mlflow/traces/{id}/…` or from the trace data artifact.
  - Trace search is limited to 25 QPS per workspace.
  - The experiment is found from `experiment_id` on Knowledge Assistants and Supervisors. For custom agents it comes from the logged model's run, or the endpoint's `MLFLOW_EXPERIMENT_ID` env var **(verify)**.
- **Inference tables:** `ai_gateway.inference_table_config` names the UC table `<cat>.<sch>.<prefix>_payload`. Reading rows needs a SQL warehouse (`POST /api/2.0/sql/statements`) and `SELECT`. This is optional, because it carries prompts and PII.
- **Usage:** `system.serving.endpoint_usage` and `served_entities` are readable by **account admins only**, so ACT does not use them by default.

### 2.8 MCP
- Databricks offers **no MCP server for workspace inventory**. The managed MCPs cover Genie, AI Search, UC functions and SQL. ACT should not use MCP for discovery.
- ACT should **record** MCP URLs found in agent config, Supervisor tools of type `uc_connection`, and App resources as `dependencies.mcp_servers`.
- A UC connection with an MCP flag marks an external MCP. Read it with `GET /api/2.1/unity-catalog/connections` (`connection_type`, `options.host`; flags **(verify)**).

### 2.9 Clear-text fields that ACT must redact before storing or displaying
- `served_entities[].environment_vars`: values are often `{{secrets/…}}` references, which are safe, but plain keys appear in the wild. Keep the keys, and redact any value that is not a `{{secrets/…}}` reference.
- `functions[].routine_definition`: SQL or Python bodies can embed tokens or hosts. Store a hash and the first line only.
- `config.system_prompt` on agent services, and `instructions` on Knowledge Assistants and Supervisors: these are proprietary rather than secret. Store them, but show them only to the agent's owner and reviewers.
- `data_plane_info.query_info.authorization_details`, UC connection `options` (host, user), and any `*_plaintext` external-model fields (documented as input-only, so expect none).
- Trace and inference-table payloads hold customer PII. ACT keeps only IDs, timings and status, never the text.

---

## 3. Field mapping to `SourceAgent`

| SourceAgent | Endpoint agent | App agent | Knowledge Assistant / Supervisor | Agent service (Beta) |
|---|---|---|---|---|
| `platform` | `databricks` | same | same | same |
| `native_id` | `<ws_id>:endpoint:<endpoint id>` | `<ws_id>:app:<id>` | `<ws_id>:ka:<id>` / `<ws_id>:sa:<supervisor_agent_id>` | `<metastore_id>:agent-service:<cat.sch.name>` |
| `name` | `name` | `name` | `display_name` | the last segment of `name` |
| `description` | `description`, or the UC model `comment` | `description` | `description` | `comment` |
| `version` | the live `entity_version` (from the route with the most traffic) | `active_deployment.deployment_id` (short form) | `"1"`. No versions are exposed. | `"1"` |
| `owner` | the `owner` tag, then the UC model `owner`, then `creator` | the `owner` tag, then `creator` | `creator` | `created_by`. `owner` is null in the Beta. |
| `status` | `READY` if `state.ready=READY`, otherwise `NOT_READY`; `UPDATE_FAILED` if the config update failed | `app_status.state` | `state` | `REGISTERED` |
| `last_updated` | `last_updated_timestamp` (ms, convert to ISO) | `update_time` | `create_time` | not available |
| `url` | `https://<ws>/ml/endpoints/<name>` | `url` | `https://<ws>/agents/…` (UI link, **verify**) | `https://<ws>/explore/data/…` |
| `provider` | `"Databricks"` | same | same | `"External (via Databricks)"` |
| `tools` | model-version `function` deps, plus `DatabricksFunction`/Genie/AI Search resources | `resources` of type genie_space, serving_endpoint and job | `/tools[]` | none |
| `data_scopes` | table deps, vector index source tables, and effective permissions | `uc_securable` resources | `table`/`volume`/`catalog`/`schema` tools | none |
| `systems` | external hosts from UC connections | external hosts from connections and secrets | `uc_connection` hosts | the connection host |
| `value_limits` | none (taken from tags if the bank uses them) | same | same | same |
| `criteria` | `ai_gateway.guardrails`, `rate_limits`, any HITL tag, scale | none | none | none |
| `identity` | section 2.6 | the app SP, plus OBO scopes | the system SP | `{type:"external", principal: connection}` |
| `versions` | UC `model_versions[]` with `{version, created: created_at}` | `/deployments[]` | `[{"1", create_time}]` | `[{"1", null}]` |
| `dependencies` | `models`: foundation or external endpoints called. `mcp_servers`: MCP URLs. | the same, taken from resources | sub-agents and Knowledge Assistants | none |
| `extra` | `endpoint_name`, `uc_model`, `task`, `agent_signal`, `inference_table`, `experiment_id`, `workspace_id`, `workspace_host`, `cloud` | `service_principal_client_id` | `endpoint_name`, `experiment_id` | `agent_service_type`, `base_path` |

De-duplication: a Knowledge Assistant or Supervisor *owns* its endpoint, so merge it with the endpoint match and keep one record. An agent service that points at a Databricks App URL should link to the App record rather than create a duplicate.

---

## 4. Auth options ranked by friction (lowest first)

**Facts that shape every option.**
- The Domino run token here is RS256 with `iss=https://se-demo.domino.tech/auth/realms/DominoRealm`. The issuer's `.well-known/openid-configuration` and `jwks_uri` are **publicly reachable** (both checked).
- Its `aud` list is `domino-platform, flyteadmin, toolkit-client, grafana-client, grafana-workload-client, account`, with `azp=domino-play`.
- Its **lifetime is 300 s**. It carries `sub`, `email` and `preferred_username`.
- Databricks requires RS256/ES256 and accepts no custom `audience` and no `actor_token`. The exchanged token **inherits the JWT's `exp`** (so it lasts at most 5 minutes) and comes with no refresh token.

### Option A: federated service principal (secretless M2M). **Recommended primary.**
- **What ACT registers:** a **service principal federation policy** on a dedicated SP `act-reader`:
  ```json
  {"oidc_policy":{"issuer":"https://se-demo.domino.tech/auth/realms/DominoRealm",
    "audiences":["domino-platform"], "subject":"<sub of the Domino service account that runs ACT>", "subject_claim":"sub"}}
  ```
  - **Pinning:** the policy accepts a JWT only if `iss` matches, **some** `aud` entry equals one listed audience, and `<subject_claim>` equals `subject` exactly. Pinning `sub` to one Domino service account means other Domino users' runs cannot use the policy.
  - **Audience caveat:** `domino-platform` sits in every Domino token, so the audience adds little. The stronger setup has the Domino admin add a Keycloak audience mapper (`aud=databricks-act`) to that service account's tokens, and lists only that audience.
  - If `audiences` is left out, it defaults to the Databricks account ID, which Domino tokens never carry, so an explicit audience is required.
- **Exchange (per workspace):**
  ```
  POST https://<ws-host>/oidc/v1/token
  grant_type=urn:ietf:params:oauth:grant-type:token-exchange
  subject_token=<fresh JWT from :8899/access-token>
  subject_token_type=urn:ietf:params:oauth:token-type:jwt
  scope=all-apis
  client_id=<SP application id>
  ```
  - The response is `{access_token, token_type:"Bearer", expires_in≤300, scope}`.
  - ACT re-reads `:8899` and re-exchanges before each batch. A crawl Job can do this in its loop.
  - The account-level exchange is `https://<account-host>/oidc/accounts/<account-id>/v1/token`.
- **Whose `sub`:**
  - The App process gets the token of the identity the App runs as. The crawl **Domino Job must be launched as that same service account**, not as the clicking user.
  - Create a Domino service account `act-connector`. Publish ACT under it, or start crawl Jobs with its token.
  - Verify which `sub` the App's `:8899` returns, and use the SA user ID (24 hex characters).
- **User steps:** none after setup. The wizard shows "Connected via Domino identity (no secrets stored)."
- **Admin setup (once, by the Databricks account admin), as one script:**
  1. Create SP `act-reader`.
  2. Create the federation policy.
  3. Assign the SP to the chosen workspaces (USER role, no admin).
  4. Grant `BROWSE` on each catalog (metadata only, no data). Grant `USE CATALOG` and `USE SCHEMA`, plus `EXECUTE`/`READ METADATA` on agent services if those are used.
  5. Give the SP `CAN_VIEW` on serving endpoints, apps, Knowledge Assistants and Supervisors through a workspace group `act-readers`. Use group ACLs where possible; endpoints need per-object ACLs **(verify whether a workspace-wide viewer exists; this is the main grant pain)**.
  6. Optionally grant `SELECT` on inference tables, and Experiment `CAN_READ` for traces.
  - Limits: 20 federation policies per SP and 20 account policies.
- **One-click artifact:** ACT renders a Terraform module (with `databricks` account and workspace providers) or a bash script on `databricks` CLI ≥0.250, prefilled with issuer, audience, subject and redirect URL. The script uses:
  - `databricks account service-principals create`
  - `databricks account service-principal-federation-policy create <sp-id> --json …`
  - `databricks account workspace-assignment update`
  - per workspace, `databricks grants update catalog …` and `databricks permissions update serving-endpoints …`
  - The Terraform equivalents are `databricks_service_principal`, `databricks_service_principal_federation_policy` **(verify the resource name)**, `databricks_mws_permission_assignment`, `databricks_grants` and `databricks_permissions`.
  - The script **ends by printing a connection code**: base64 JSON `{cloud, account_id, sp_client_id, workspaces:[{id,host}]}`. It holds no secret.
- **Auto-verify:** exchange the token, call `GET /api/2.0/preview/scim/v2/Me` (it must return the SP), then probe each permission (section 6).
- **ACT stores:** account ID, cloud, SP client ID and workspace hosts. **No secrets.**

### Option B: OAuth U2M "Sign in with Databricks" (popup). **Fallback.**
- **Registration:** an account-level **custom OAuth app integration**: `POST /api/2.0/accounts/{account_id}/oauth2/custom-app-integrations` with `{name:"ACT", redirect_urls:["https://se-demo.domino.tech/apps/<app-id>/api/oauth/databricks/callback"], confidential:false, scopes:["all-apis","offline_access"], token_access_policy:{access_token_ttl_in_minutes, refresh_token_ttl_in_minutes}}`.
  - It is a public client with PKCE (S256), so **no client secret** exists.
  - Redirect URIs must match exactly, so each Domino App ID (and each environment) needs its own entry, and the URL changes if the App is re-created.
  - An account admin must register it once. ACT includes this in the same script as Option A.
- **Flow:** the popup goes to the account-level authorize endpoint `https://<account-host>/oidc/accounts/<account-id>/v1/authorize` (with `response_type=code`, `scope=all-apis offline_access`, `code_challenge`, `state`). The user signs in through the bank's SSO, and the code is exchanged at `/v1/token`.
  - The access token lasts 1 hour, and the refresh token's lifetime is set by `token_access_policy`.
  - An account-level token works on the workspace APIs of every workspace the user can access.
- **ACT stores:** the refresh token, encrypted (a long-lived secret, but scoped to the user and revocable).
- **Visibility:** ACT sees only what that user can see. A non-admin user **cannot list workspaces** through the account API, so ACT falls back to the connection code or to pasting a workspace URL (section 5).
- **Use it when:** the bank refuses external federation, or for a quick demo by a user who already has broad visibility.

### Option C: account-wide federation policy (per-user, secretless)
- **Policy:** issuer = Domino, `subject_claim:"email"`. Databricks then maps the Domino user's email to the Databricks user with the same name, so ACT can act as the **viewing** user with no sign-in at all.
- **Requirement:** ACT needs the viewer's own Domino JWT in the App, through identity propagation **(verify)**.
- **Risk:** it trusts *every* Domino user (and every Domino admin able to mint tokens) to act as any matching Databricks user. Banks will likely reject it. List it as an option, but do not recommend it.

### Option D: OAuth M2M client secret (last resort)
- An SP OAuth secret: the client ID and secret are exchanged at `/oidc/v1/token` (`grant_type=client_credentials`, `scope=all-apis`).
- The secret is long-lived, lasting up to 730 days, and ACT stores it encrypted. Use it when a Domino-hosted issuer is not reachable from Databricks, for example in air-gapped or private Keycloak deployments.
- PATs are deliberately not offered.

### Azure variant (zero typed URL)
- **Flow:** "Sign in with Microsoft" (MSAL popup, an Entra app registration with redirect = the ACT URL). ACT then lists `Microsoft.Databricks/workspaces` across subscriptions through Azure Resource Graph, which gives `properties.workspaceUrl` and `workspaceId`.
- **Token:** ACT gets a token for the Azure Databricks resource `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default`, and the workspace APIs accept it.
- **Federation:** for no secrets, the Entra app can use a **federated credential** that trusts Domino's issuer (an Entra workload identity federation).
- This option also works with Option A, because Databricks on Azure also supports Databricks federation policies.

---

## 5. Auto-discovery of workspaces with no typed URL

**Account hosts per cloud**

| Cloud | Account host |
|---|---|
| AWS | `accounts.cloud.databricks.com` |
| Azure | `accounts.azuredatabricks.net` |
| GCP | `accounts.gcp.databricks.com` |
| GovCloud and China | separate hosts **(verify)** |

Every account API and account OIDC endpoint also needs the **account ID** (a UUID) in its path.

**Can ACT find the account ID without asking?**
- ACT cannot learn it from nothing. No documented "which accounts am I in" endpoint exists. The CLI's no-host `databricks auth login` workspace picker is not a documented public API.
- **From any workspace URL:** the SDKs auto-discover `account_id`, `workspace_id` and `cloud` from the host's unauthenticated **`/.well-known/databricks-config`** host-metadata endpoint. The schema is undocumented and the host types are `workspace|account|unified` (third-party report). So "paste any Databricks link from your browser tab" can yield the cloud and account ID **(verify the response shape live)**.
- **From the connection code:** the provisioning script prints the account ID, so the user pastes one opaque code. This is the recommended path.
- **On Azure:** ARM gives every workspace URL after a Microsoft sign-in.

**Listing workspaces**
- `GET https://<account-host>/api/2.0/accounts/{account_id}/workspaces` (API scope `provisioning`) returns `[{workspace_id, workspace_name, deployment_name, workspace_status, cloud, aws_region|location, azure_workspace_info, creation_time}]`.
  - It requires an **account admin** token. ACT should not give `act-reader` account admin.
  - The workspace host comes from `deployment_name`, for example `https://<deployment_name>.cloud.databricks.com` on AWS.
- The alternative is the system table `system.access.workspaces_latest`, which holds `account_id`, `workspace_id`, `workspace_name`, `workspace_url` and `status` (Public Preview). It needs a SQL warehouse and a grant on `system.access`.
- **Recommendation:** ACT takes the workspace list from the connection code. When an admin signs in with Option B, ACT also calls the account list once and **refreshes it at each sync** whenever an admin session is available. Re-running the script picks up new workspaces.

---

## 6. Wizard steps and failure table

**Steps.** The happy path is 2 clicks plus 1 paste, with no URL typed.

| # | Step | What the user does | What ACT does |
|---|---|---|---|
| 1 | Card | Clicks the "Databricks" card and picks a cloud (AWS / Azure / GCP, auto-selected when a code is present). | none |
| 2 | Set up | Clicks **"Generate setup script"** if not yet connected. A Terraform or CLI tab opens, prefilled. The user hands it to the account admin, or runs it if they are the admin. | Polls nothing. The step waits. |
| 3 | Connect | Pastes the **connection code**. Alternatively, on Azure the user clicks "Sign in with Microsoft", or clicks "Sign in with Databricks" (Option B) after pasting any Databricks URL. | Decodes the code and shows "Account `…a1f`, 4 workspaces, SP act-reader". |
| 4 | Verify | Watches the automatic checks run. | Per workspace: token exchange; `scim/v2/Me`; list endpoints, apps, Knowledge Assistants, Supervisors and catalogs; one `effective-permissions` call. Shows green ticks or fixes. |
| 5 | Scopes | Accepts the defaults. | Workspaces × catalogs, all ticked. Agent services and traces are optional toggles. |
| 6 | Discover | Watches progress. | Runs as a Domino Job. Shows per-workspace counts per kind (endpoint, app, KA, Supervisor, agent service). |
| 7 | Review and import | Imports from the review table. | Uses the shared wizard (import-wizard-plan). |

**Failure table.** ACT shows the plain-language fix.

| Where | Error (HTTP / code) | Plain-language fix |
|---|---|---|
| Token exchange | 400 `invalid_request`, "no matching federation policy" **(record the exact text)** | "Databricks doesn't trust this Domino identity yet. Re-run the setup script; check the issuer is `https://se-demo.domino.tech/auth/realms/DominoRealm`." |
| Token exchange | 400/401 `invalid_grant` (subject or audience mismatch, or JWT expired) | "The policy is pinned to a different Domino account. ACT runs as `<sa>`; update `subject` to `<sub>`." |
| Token exchange | 400 `invalid_client` (unknown `client_id`) | "The service principal isn't in this workspace. Assign `act-reader` to workspace `<name>`." |
| Token exchange | Network error or 5xx while fetching JWKS (Databricks side) | "Databricks can't reach Domino's sign-in keys. Ask the admin to allow `se-demo.domino.tech`, or paste the JWKS into the policy." |
| Any API | 401 `UNAUTHENTICATED` / "Invalid access token" | "The session expired. ACT will retry; if it repeats, reconnect." |
| Any API | 403 `PERMISSION_DENIED` | Per-permission fix, for example "Grant `CAN_VIEW` on serving endpoints to group `act-readers`." |
| Any API | 429 `REQUEST_LIMIT_EXCEEDED` | "Databricks is rate-limiting; ACT is slowing down." (automatic) |
| Account list | 403 `PERMISSION_DENIED` on `/accounts/{id}/workspaces` | "Only account admins can list workspaces; ACT used the workspaces from your setup code." |
| Agent services | 404 `FEATURE_DISABLED` / `ENDPOINT_NOT_FOUND` **(record it)** | "Agent services (Beta) aren't enabled. Optional: turn on Unity Gateway beta in Account console → Previews." |
| Knowledge Assistants / Supervisors | 404 / 501 `NOT_IMPLEMENTED` | "Agent Bricks isn't available in this workspace or region; skipped." |
| U2M | `redirect_uri` mismatch (browser error page) | "ACT's address changed. Update the redirect URL in the ACT OAuth app (setup script, step 2)." |
| U2M | `access_denied` (consent refused) | "Sign-in was cancelled. Try again or use the admin setup script." |
| UC | 403 on `effective-permissions` | "Grant `BROWSE` on catalog `<c>` so ACT can read data scopes (no data access)." |

---

## 7. Intake of one agent (extra calls)

| Agent kind | Extra calls |
|---|---|
| Endpoint agent | `GET serving-endpoints/{name}`; `GET permissions/serving-endpoints/{id}`; `GET unity-catalog/models/{entity_name}`; `GET …/models/{entity_name}/versions` (all versions and dependencies); per dependency, `GET functions/{fn}`, `GET tables/{t}` and `effective-permissions` for the identity; optionally download `MLmodel` for `resources` and `auth_policy`; and the last 50 traces from `POST /api/3.0/mlflow/traces/search` for the run history (`list_runs`). |
| App | `GET apps/{name}`; `GET apps/{name}/deployments`; `GET permissions/apps/{name}`; `effective-permissions` for `service_principal_name` on each `uc_securable`; follow `serving_endpoint` resources to models and agents. |
| Knowledge Assistant / Supervisor | `GET supervisor-agents/{id}`; `GET …/tools`; follow tools (Genie space, KA, UC function, `uc_connection`, `app`); endpoint `{endpoint_name}`; traces from `experiment_id`. |
| Agent service | `GET agent-services/{full}`; `GET permissions/AGENT_SERVICE/{full}`; `GET connections/{name}` for the host. |
| All kinds | Map the creator email to a Domino user; fill `criteria` from `ai_gateway.guardrails` and `rate_limits`. |

---

## 8. Recorded-HTTP fixture list (contract tests)

These replace `fixtures/databricks/agent-services.json`. Use real shapes with bank-like values. Each file is one request and its response.

1. `oidc/token-exchange.ok.json`, plus `.no-policy.json`, `.subject-mismatch.json` and `.invalid-client.json`
2. `scim/me.sp.json`
3. `account/workspaces.list.json` (3 clouds) and `.403.json`
4. `wellknown/databricks-config.json` (host metadata)
5. `serving-endpoints/list.json`, mixing 2 agent endpoints, 1 foundation model, 1 external model and 1 KA endpoint
6. `serving-endpoints/get.agent.json`, `.update-failed.json` and `.env-secret-plaintext.json` (redaction test)
7. `permissions/serving-endpoints.json`
8. `apps/list.page1.json` and `.page2.json` (pagination), plus `apps/get.json` and `apps/deployments.json`
9. `knowledge-assistants/list.json` and `.404.json`
10. `supervisor-agents/list.json`, `supervisor-agents/tools.json`
11. `uc/agent-services/list.json` (to record live; the wrapper is unknown), `get.json` (with `full_name` and `owner` null) and `.feature-disabled.json`
12. `uc/permissions/agent-service.json`
13. `uc/catalogs.json`, `uc/schemas.json` and `uc/metastore_summary.json`
14. `uc/models/list.json`, `uc/models/versions.json` (with dependencies)
15. `uc/functions/list.json` (with `routine_definition` to redact)
16. `uc/effective-permissions.table.json`
17. `uc/connections/get.json`
18. `mlflow/traces/search.page1.json` and `.page2.json`
19. `errors/429.json` (with `Retry-After`), `errors/401.json` and `errors/403.json`

---

## 9. Risks, unknowns, sources

**Risks.**
1. **The current fixture and adapter are mostly fiction.**
   - The endpoint is real but **Beta and preview-gated**. It covers **external** agents only.
   - It has none of the fields the adapter reads (`agent_service_id`, `owner`, `endpoint_name`, `identity`, `properties.*`, ms timestamps).
   - Built as it stands, the adapter would show zero agents for nearly every bank.
   - The fix is to rebuild the adapter as a union of endpoints, apps, Knowledge Assistants and Supervisors, with agent services as an optional extra.
2. **Grant friction for least privilege.**
   - UC metadata reads are easy, because `BROWSE` reads metadata without giving data access.
   - Serving endpoints, apps and Agent Bricks use per-object ACLs, so they need group ACLs scripted per object. New objects created after setup are invisible until someone grants access again. A periodic re-run of the script, or an endpoint-creation convention, is needed.
   - Listing workspaces needs account admin.
3. **The federation identity and lifetime.**
   - Domino tokens last 300 s, so the Databricks token does too, with no refresh. A long crawl must re-exchange often (the token endpoint allows 40 RPS per workspace).
   - `sub` must belong to a dedicated Domino service account that both the App and the crawl Jobs run as **(verify which identity an App's `:8899` returns)**.
   - `aud` cannot be customised without a Keycloak mapper.
   - The Domino issuer must be reachable from Databricks' control plane. Private deployments need pasted JWKS, which must be updated whenever Keycloak rotates its keys.
4. **Churn.**
   - The doc paths moved (`generative-ai/` to `agents/`).
   - The Supervisor API reached end of life on 2026-09-30, and Knowledge Assistant and Supervisor are labelled legacy.
   - Agent Bricks and agent services APIs are Beta.
   - "Unity Gateway" and "AI Gateway" naming is inconsistent.
   - Pin the SDK version and tolerate 404 on each source.
5. **Unknowns to record live:**
   - the exact agent `task` values
   - the agent-services list wrapper and pagination, and its disabled-feature error
   - the `.well-known/databricks-config` schema
   - the Genie spaces list API
   - whether serving-endpoints list paginates
   - the exact text of token-exchange errors
   - the Terraform federation-policy resource name
   - the GovCloud account host

**Sources (all accessed 2026-11-12)**
- Agent services (Beta), updated 2026-09-11: https://docs.databricks.com/aws/en/ai-gateway/agent-services ; https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/agent-services ; https://docs.databricks.com/aws/en/ai-gateway/agent-services-beta
- DAIS 2026 Unity Gateway post (2026-06-16): https://www.databricks.com/blog/ai-governance-data-ai-summit-2026-whats-new-unity-ai-gateway ; Agent Bricks at DAIS 2026: https://www.databricks.com/blog/agent-bricks-dais-2026
- MCP services: https://docs.databricks.com/gcp/en/agents/agent-framework/mcp-services ; managed MCP (updated 2026-11-10): https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp
- Federation policy (updated 2026-10-02): https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-policy ; token exchange (updated 2026-10-23): https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-exchange
- OAuth U2M (updated 2026-11-10): https://docs.databricks.com/aws/en/dev-tools/auth/oauth-u2m ; custom app integrations: https://docs.databricks.com/api/o-auth/v1/create-custom-o-auth-app-integration.md ; https://databricks-sdk-py.readthedocs.io/en/latest/account/oauth2/custom_app_integration.html
- CLI auth: https://docs.databricks.com/aws/en/dev-tools/cli/authentication ; CLI workspace picker PR: https://github.com/databricks/cli/pull/7522 ; SDK host metadata: https://github.com/databricks/databricks-sdk-py/blob/main/README.md ; https://github.com/OffeneDatenmodellierung/databricks-rs/issues/25
- Account workspaces list: https://docs.databricks.com/api/account/workspaces/list ; workspaces system table: https://docs.databricks.com/aws/en/admin/system-tables/workspaces
- Serving endpoints: https://docs.databricks.com/api/workspace/servingendpoints/get ; https://docs.databricks.com/api/model-serving/v1/serving-endpoint/object.md
- Apps list: https://docs.databricks.com/api/workspace/apps/list ; custom agents on Apps: https://developers.databricks.com/docs/agents/custom-agents
- Knowledge Assistants: https://docs.databricks.com/api/knowledge-assistants/v1/list-knowledge-assistants.md ; Supervisor Agents: https://docs.databricks.com/api/workspace/supervisoragents/getsupervisoragent ; https://databricks-sdk-py.readthedocs.io/en/stable/workspace/supervisoragents/supervisor_agents.html ; Supervisor API end of life: https://docs.databricks.com/aws/en/agents/agent-bricks/supervisor-api
- Querying agents: https://docs.databricks.com/aws/en/agents/custom-agents/query-agent ; MLflow 3 trace query: https://docs.databricks.com/aws/en/mlflow3/genai/tracing/observe-with-traces/query-via-sdk
- Limits: https://docs.databricks.com/aws/en/resources/limits ; inference tables: https://docs.databricks.com/aws/en/ai-gateway/inference-tables
- Domino Keycloak discovery (checked live): https://se-demo.domino.tech/auth/realms/DominoRealm/.well-known/openid-configuration
