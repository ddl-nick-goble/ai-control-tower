# Provider research: Snowflake (Cortex Agents, Snowflake Intelligence / CoWork, managed MCP)

Researched 2026-11-12. Research only, no app code. Sources are in section 9, all accessed 2026-11-12.

**Bottom line.**
- **Auth (primary):** Workload Identity Federation (WIF). A Snowflake `TYPE = SERVICE` user trusts the Domino Keycloak issuer, pinned to one `SUBJECT` (a dedicated Domino service account) and an `OIDC_AUDIENCE_LIST`. ACT sends the Domino run JWT directly as `Authorization: Bearer WIF.OIDC.<jwt>` on the SQL API and the REST API. **Nothing secret is stored.**
- **Setup:** one ACT-generated SQL worksheet. The admin pastes it into Snowsight and clicks Run All. Its last row is a "Finish in ACT" link that carries the account host and a nonce, so **the user types nothing**.
- **Inventory:** `SHOW AGENTS IN ACCOUNT` (SQL API) plus `DESCRIBE AGENT` (the agent spec JSON) and `SHOW VERSIONS IN AGENT`. Traces come from `SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS`. `ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY` cross-checks the inventory for agents ACT cannot see.
- **Biggest caveat:** visibility depends on grants and fails silently. Agent tools also run with the **caller's** roles, so an agent's real data scope depends on who calls it (see section 9).

---

## 1. What counts as an "agent"

| Product / object | SQL object, scope | Agent for ACT? | Status |
|---|---|---|---|
| **Cortex Agent** | `AGENT`, schema-level (`DB.SCHEMA.NAME`); `CREATE AGENT ... FROM SPECIFICATION $$yaml$$` or `FROM @stage` (`agent_spec.yaml`) | **Yes, primary.** It holds the model, instructions, tools, tool_resources, MCP servers and budget. | GA 2025-11-04. Object REST API was preview 2025-09-02; it is GA with the agent object. |
| Secure / temporary / personal-DB agents | `CREATE SECURE AGENT`, `TEMPORARY AGENT`, agents in a user's personal database | Secure agents: yes, but the spec is NULL for non-owners. Temporary agents: no, they are session-only and unversioned. Personal-DB agents: yes if visible (often not). | "Object enhancements" GA 2026-09-16 |
| Agent versions | `ALTER AGENT .. COMMIT` gives `VERSION$N`, plus one editable `LIVE`, aliases and `DEFAULT_VERSION` | Map to `versions` | Documented. The status is not labelled; treat it as GA with the agent object. |
| **Snowflake Intelligence** (docs now say "Snowflake CoWork") | One account object `SNOWFLAKE INTELLIGENCE SNOWFLAKE_INTELLIGENCE_OBJECT_DEFAULT` holding a curated agent list. The legacy `SNOWFLAKE_INTELLIGENCE.AGENTS` schema is deprecated. | **Not a separate agent.** It is the end-user surface. Membership is a "published to employees" signal for `criteria`. | GA 2025-11-04. CoWork features (Automations GA 2026-09-11, Deep Research GA 2026-07-07) |
| Cortex Agents Coding Agent | `CODING_AGENT` MCP tool / run-API config | Not a stored object. Seen only in traces. | GA 2026-08-26 |
| **External Agent** | `EXTERNAL AGENT` (+ `ADD VERSION`) | **Optional.** A registration of an app running outside Snowflake, used for AI Observability. List it as an agent with `provider="external"`. | Status not stated |
| Snowflake-managed MCP server | `MCP SERVER` (schema-level), tools of type `CORTEX_AGENT_RUN`, `CORTEX_ANALYST_MESSAGE`, `CORTEX_SEARCH_SERVICE_QUERY`, `SYSTEM_EXECUTE_SQL`, `GENERIC`, `CODING_AGENT` | **Tool surface, not an agent.** Map to `systems` and exposure. A `SYSTEM_EXECUTE_SQL` tool is a red flag. | GA 2025-11-04. External OAuth GA 2026-07-22. Background runs 2026-11-04 |
| External MCP server (connector) | `EXTERNAL MCP SERVER` + `API INTEGRATION (API_PROVIDER = EXTERNAL_MCP)` | Dependency of an agent (`mcp_servers`). It holds OAuth secrets. | Status not stated |
| Tools inside a spec | `cortex_analyst_text_to_sql` (semantic view or YAML), `cortex_search`, `generic` (procedure/UDF), `web_search`, data_to_chart, code execution (GA 2026-10-05), skills, toolsets | Map to tools and data_scopes | Analytical search is preview |

Native Apps can ship agents and MCP servers (GA 2026-08-07). They appear inside the app's database, and run records are redacted by provider.

---

## 2. Inventory APIs

### 2.1 Transport
- **SQL API (preferred for everything):** `POST https://{host}/api/v2/statements`
  - Headers: `Authorization: Bearer …`, `X-Snowflake-Authorization-Token-Type: WORKLOAD_IDENTITY_FEDERATION|OAUTH|KEYPAIR_JWT`, `Content-Type`/`Accept: application/json`, `User-Agent: ACT/1.0`.
  - Body: `{"statement":"SHOW AGENTS IN ACCOUNT","timeout":60,"role":"ACT_READER","warehouse":"ACT_WH"}`.
  - **200** returns `{code:"090001", statementHandle, resultSetMetaData:{numRows, rowType:[{name,type}], partitionInfo:[…]}, data:[["…",…]]}`. All values are strings.
  - **202** returns `{code:"333334", statementHandle, statementStatusUrl}`. Poll `GET /api/v2/statements/{handle}`, then fetch further partitions with `?partition=N`.
  - Errors are JSON with `code`, `sqlState`, `message`: 422 for SQL errors, 401 for auth, 429 for throttling.
  - `SHOW` and `DESCRIBE` **need no running warehouse**. The observability and ACCOUNT_USAGE queries do.
- **REST API (per-schema only):** `https://{host}/api/v2/databases/{db}/schemas/{schema}/agents`. Same auth headers; set the role with `X-Snowflake-Role`.
- `{host}` is the regionless `{org}-{account}.snowflakecomputing.com`. Use hyphens in hostnames; an underscore in the account name becomes `-`. With PrivateLink it is `{org}-{account}.privatelink.snowflakecomputing.com`.

### 2.2 List agents
**SQL:**
```sql
SHOW AGENTS [LIKE '<pat>'] [IN ACCOUNT | DATABASE <db> | SCHEMA <db>.<s>] [STARTS WITH '<s>'] [LIMIT <n> [FROM '<name>']];
```
- Columns: `created_on, name, database_name, schema_name, owner, comment, profile` (JSON with `display_name`, `avatar`, `color`) and `is_secure`.
- **Cap: 10,000 rows**, with no Information Schema fallback. For large accounts, page by database (`SHOW AGENTS IN DATABASE x`), then `LIMIT 10000 FROM '<last name>'`.
- It returns **only agents where the role holds some privilege** (OWNERSHIP, USAGE, MODIFY, MONITOR), plus a privilege on the parent db and schema. There is **no error when the list is incomplete**.
- Tip: `SHOW AGENTS IN ACCOUNT ->> SELECT "database_name","schema_name",count(*) FROM $1 GROUP BY 1,2` counts agents per schema in one call. Column names are lowercase and quoted.

**REST:** `GET …/databases/{db}/schemas/{schema}/agents?like=&fromName=&showLimit=1..10000`. It returns `[{name, database, schema, created_on, owner, comment, profile}]`; the `Link` header carries the next page. It needs the db and schema, so it suits re-checks, not discovery.

### 2.3 Agent detail (spec, tools, models, owner)
- **`DESCRIBE AGENT db.s.name`**
  - Columns: `name, database_name, schema_name, owner, comment, profile, agent_spec` (JSON string) and `created_on`.
  - `DESCRIBE AS RESOURCE AGENT x` returns the whole thing as one JSON document.
  - **For secure agents, `agent_spec` is NULL unless the owner role is active.**
- **REST:** `GET …/agents/{name}` returns `{name, database_name, schema_name, owner, created_on, agent_spec}`.
- **`agent_spec` shape (what ACT parses):**
```json
{"models":{"orchestration":"claude-sonnet-4-6"},
 "orchestration":{"budget":{"seconds":60,"tokens":16000},"tool_not_accessible":"accept"},
 "instructions":{"response":"…","orchestration":"…","system":"…","sample_questions":[{"question":"…"}]},
 "tools":[{"tool_spec":{"type":"cortex_analyst_text_to_sql","name":"payments_analyst","description":"…"}},
          {"tool_spec":{"type":"cortex_search","name":"policy_docs"}},
          {"tool_spec":{"type":"generic","name":"refund","input_schema":{…}}}],
 "tool_resources":{
   "payments_analyst":{"semantic_view":"RISK.SEM.PAYMENTS","execution_environment":{"type":"warehouse","warehouse":"AGENT_WH","query_timeout":30}},
   "policy_docs":{"search_service":"RISK.DOCS.POLICY_SEARCH","max_results":5,"filter":{…}},
   "refund":{"type":"procedure","identifier":"PAY.OPS.ISSUE_REFUND","execution_environment":{"type":"warehouse","warehouse":"OPS_WH"}}},
 "mcp_servers":[{"server_spec":{"name":"DB.S.JIRA_MCP"}}]}
```
  - The spec limit is 100,000 bytes.
  - `models.orchestration` may be absent, meaning Snowflake picks automatically ("auto").
- **Versions:** `SHOW VERSIONS IN AGENT db.s.name` returns one row per `VERSION$N` / `LIVE`, with alias, `created_on`, comment and `spec_file_path`. **Verify the column names with a recorded fixture.** The default version is set by `ALTER AGENT … SET DEFAULT_VERSION`, and it defaults to the latest commit. Runs can target `…/agents/{name}/versions/{v}:run`.
- **Status and times:** there is **no status field and no `last_altered`**. Use `created_on` and the newest version's `created_on`, and have ACT hash `agent_spec` to detect changes.
- **Snowflake Intelligence membership:** `SHOW SNOWFLAKE INTELLIGENCES` confirms the object exists. **No documented command lists its agents** (USAGE "lets users view the list"). Probe `DESCRIBE SNOWFLAKE INTELLIGENCE SNOWFLAKE_INTELLIGENCE_OBJECT_DEFAULT` and record a fixture. Treat it as unknown.
- **Tags (owner, cost center):** use `SYSTEM$GET_TAG` per agent, or the `AGENT_TAGS` column in usage history.

### 2.4 Tool dependencies
| Tool | Read with | Gives |
|---|---|---|
| Semantic view | `DESCRIBE SEMANTIC VIEW x` (needs a privilege on it), or just the identifier from the spec | Base tables and the metrics exposed |
| Cortex Search | `SHOW CORTEX SEARCH SERVICES LIKE 'x' IN SCHEMA …` / `DESCRIBE CORTEX SEARCH SERVICE x` | Source query and tables, warehouse |
| Custom tool (proc/UDF) | `SHOW PROCEDURES LIKE …` / `DESCRIBE PROCEDURE x(sig)` | **`EXECUTE AS OWNER|CALLER`**, owner. Owner's rights means privilege escalation, which drives the tier. |
| External MCP | `SHOW EXTERNAL MCP SERVERS IN ACCOUNT` / `DESCRIBE EXTERNAL MCP SERVER x` | URL, state, API integration (a `systems` entry) |
| Managed MCP | `SHOW MCP SERVERS IN ACCOUNT` (`created_on,name,database_name,schema_name,owner,comment`), `DESCRIBE MCP SERVER x` (spec) | Which agents are exposed to outside clients; `SYSTEM_EXECUTE_SQL` |
| Lineage | Data lineage for Cortex Agents (2026-09-02); likely `SNOWFLAKE.CORE.GET_LINEAGE('db.s.agent','AGENT',…)` | Downstream tables. **Unverified.** |

The ACT role usually lacks privileges on these objects. **The minimum is the identifiers from the spec**, and the privileged reads are best-effort.

### 2.5 Grants (data scopes, who can call)
- `SHOW GRANTS ON AGENT db.s.name` returns `created_on, privilege, granted_on, name, granted_to, grantee_name, grant_option, granted_by_role_type, granted_by`. It shows **who can invoke** (USAGE) and who monitors.
- `SHOW GRANTS ON SEMANTIC VIEW|CORTEX SEARCH SERVICE|PROCEDURE|WAREHOUSE …` shows the same for each tool object.
- **Account-wide, one query (preferred):** `SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_ROLES` (database role `SECURITY_VIEWER`, about 2 h latency):
```sql
SELECT privilege, granted_on, table_catalog, table_schema, name, grantee_name, granted_by, created_on
FROM SNOWFLAKE.ACCOUNT_USAGE.GRANTS_TO_ROLES
WHERE deleted_on IS NULL AND granted_on IN ('AGENT','SEMANTIC_VIEW','CORTEX_SEARCH_SERVICE','PROCEDURE','FUNCTION','WAREHOUSE');
```
  - Confirm the `granted_on` literals against a fixture.
  - `GRANTS_TO_USERS` maps roles to people, so it answers "who can call this agent".
- **Identity model:** the agent has **no identity of its own**. Tools run under the **caller's session roles**. Procedures run as owner or caller according to their definition. The owner role only owns the object.

### 2.6 Runs, threads, traces
- **Traces (main source):**
```sql
SELECT timestamp, start_timestamp, trace:trace_id::string trace_id, record:name::string span,
       record_attributes, resource_attributes
FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS('RISK','AGENTS','PAYMENTS_AGENT','CORTEX AGENT'))
WHERE timestamp > :since ORDER BY timestamp;
```
  - One turn is one trace. Spans are named `Agent`, `AgentV2RequestResponseInfo`, `ReasoningAgentStepPlanning-N`, `CortexAnalystTool_*`, `SqlExecution_*`, `SystemExecuteSQLTool_*`, `CodeExecutionTool_*` and similar. Feedback rows have `record:name='CORTEX_AGENT_FEEDBACK'`. User, role and warehouse are in `RESOURCE_ATTRIBUTES`. The rows are immutable.
  - Needs MONITOR on the agent, `SNOWFLAKE.CORTEX_USER`, and a warehouse.
  - **Without `READ UNREDACTED AI OBSERVABILITY EVENTS TABLE`** (account level) ACT sees only metadata: tool names, tokens, latency, model and errors. That is enough for drift and tiering, and keeps prompt PII out of ACT. Make unredacted access opt-in.
  - The underlying table is `SNOWFLAKE.LOCAL.AI_OBSERVABILITY_EVENTS` (app role `SNOWFLAKE.AI_OBSERVABILITY_READER`).
- **Usage (account-wide, 1 h latency, 1 y retention):** `SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY` (`USAGE_VIEWER`).
  - Columns: `START_TIME, END_TIME, USER_ID, USER_NAME, USER_TAGS, REQUEST_ID, PARENT_REQUEST_ID, AGENT_DATABASE_NAME, AGENT_SCHEMA_NAME, AGENT_ID, AGENT_NAME, AGENT_TAGS, TOKEN_CREDITS, TOKENS, TOKENS_GRANULAR, CREDITS_GRANULAR, METADATA` (role, interface, warehouses, region).
  - Use it as the `list_runs` source and as a **completeness check**: any agent that appears here but not in `SHOW AGENTS` is missing grants.
  - `SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY` covers the CoWork UI.
- **Threads REST** (`GET /api/v2/cortex/threads/{id}`, `POST …/agents/{name}:run`) is per-user conversation state. **Don't use it.** ACT never runs agents.
- **Evaluations:** `GET_AI_EVALUATION_DATA`, `GET_AI_RECORD_TRACE` (GA 2026-03-13) give quality evidence for `criteria`.

### 2.7 Snowflake-managed MCP server for ACT itself?
Not helpful. It exposes only the tools an admin configured (`tools/list`, `tools/call`). Inventory would need a `SYSTEM_EXECUTE_SQL` tool, which Snowflake advises against, and it requires OAuth or a PAT anyway. The SQL API is simpler and read-only by role. Inventory MCP servers as agent exposure instead.

### 2.8 Clear-text fields that may be secret (redact before storing or showing)
- `agent_spec.instructions.*` and `sample_questions`: prompts can include internal URLs, account numbers or keys. Store a hash plus redacted text, with a regex scrubber for keys and tokens.
- `tool_resources.*.filter`, generic `input_schema` defaults and descriptions.
- `DESCRIBE EXTERNAL MCP SERVER` URL (may carry a `?token=`), and `DESCRIBE API INTEGRATION` (`OAUTH_CLIENT_ID`; whether the secret is masked is unverified, so never call it by default).
- `DESCRIBE PROCEDURE` / `GET_DDL` bodies, which can hold hard-coded credentials. Store only `EXECUTE AS` and the owner.
- `DESCRIBE MCP SERVER` GENERIC tool `config`.
- Observability `RECORD_ATTRIBUTES` (prompts, SQL, results when unredacted) and `USER_NAME` (PII). Keep aggregates.

---

## 3. Field mapping to `SourceAgent`

| SourceAgent | Snowflake source | Notes |
|---|---|---|
| platform | `"snowflake"` | |
| native_id | `"{org}-{account}/{DB}.{SCHEMA}.{NAME}"` | Quoted names keep case. A drop and recreate is detected by `created_on`. |
| name | `profile.display_name` or `name` | |
| description | `comment`, else the first line of `instructions.response` | |
| version | the `DEFAULT_VERSION` name (for example `VERSION$3`) or `LIVE` | from `SHOW VERSIONS IN AGENT` |
| owner | `owner` (a **role**) | Person: the `owner` tag, else the CREATE AGENT `user_name` from `ACCOUNT_USAGE.QUERY_HISTORY` (optional `GOVERNANCE_VIEWER`) |
| status | `"ACTIVE"`; `"SECURE_SPEC_HIDDEN"` if `is_secure` and the spec is NULL | No native status. Add `extra.published_to_intelligence`. |
| last_updated | max(version `created_on`, `created_on`) | Plus a spec-hash change |
| url | `https://app.snowflake.com/{org}/{account}/#/agents/database/{DB}/schema/{S}/agent/{NAME}` | **Path unverified.** Fall back to the account home. |
| provider | `"Snowflake Cortex"` (`"Snowflake External"` for external agents) | |
| tools | `tools[].tool_spec.{type,name,description}` + `mcp_servers[].server_spec.name` | Add `EXECUTE AS` for generic tools |
| data_scopes | semantic views, search services, proc identifiers from `tool_resources`, and their base tables when readable; also `execution_environment.warehouse` | The effective scope is the caller's role, so also store the USAGE grantees |
| systems | external MCP URLs, `web_search` enabled, API integrations, managed MCP servers exposing this agent | |
| value_limits | none native | Budget goes in `criteria` |
| criteria | `budget.{seconds,tokens}`, `tool_not_accessible`, `is_secure`, has web_search, code_exec, SYSTEM_EXECUTE_SQL, owner's-rights procs, number of USAGE grantees (PUBLIC means org-wide), in Snowflake Intelligence | Feeds the suggested scores |
| identity | `{"type":"caller_rights","principal":owner_role,"distinct":false,"invokers":[roles with USAGE]}` | Flag the "no distinct identity" rule |
| versions | `[{"version":"VERSION$N","created":…,"alias":…}]` | |
| dependencies | `{"models":[models.orchestration or "auto"],"mcp_servers":[…]}` | |
| extra | `{account_host, database, schema, profile, spec_sha256, default_version}` | |
| raw | redacted DESCRIBE row | |

---

## 4. Auth options, ranked by friction

### A. WIF with the Domino OIDC issuer (RECOMMENDED PRIMARY): zero typing, zero stored secrets
- **How it works:** ACT reads its own Domino JWT from `http://localhost:8899/access-token` (fresh per call, never logged) and sends it on every SQL API or REST call as `Authorization: Bearer WIF.OIDC.<jwt>` with `X-Snowflake-Authorization-Token-Type: WORKLOAD_IDENTITY_FEDERATION`. Snowflake checks:
  - `iss` equals `ISSUER` exactly;
  - `sub` equals `SUBJECT` exactly;
  - `aud` is in `OIDC_AUDIENCE_LIST`, or equals `snowflakecomputing.com` if the list is omitted;
  - `iat` is present;
  - the algorithm is RS/ES256-512.
  It fetches the JWKS through `/.well-known/openid-configuration`, which **must be public HTTPS** (verified HTTP 200 for se-demo by the AWS researcher), and caches keys for up to 60 min.
- **Pinning:** `SUBJECT` is the `sub` of a **dedicated Domino service account** that publishes the ACT App and owns the crawl Jobs. The (ISSUER, SUBJECT) pair must be unique per account (error `099706`). It cannot be altered; you UNSET and SET it again.
  - Always set `OIDC_AUDIENCE_LIST` to the Domino token's actual `aud` (for example, the Keycloak client id). Never rely on the shared default `snowflakecomputing.com`.
  - Any other Domino user's run carries a different `sub`, so it is rejected.
  - Optionally add an authentication policy that allows only WIF from this issuer.
- **User steps:** Connect → **Copy setup SQL** → **Open Snowsight** (`https://app.snowflake.com`, which lands in the last-used account) → paste into a new worksheet, Run All → click the link in the last row → ACT verifies. If the user isn't an admin: "Send to my admin" copies the SQL plus instructions.
- **Admin setup:** ACCOUNTADMIN, or SECURITYADMIN plus a role with MANAGE GRANTS, runs the worksheet below.
- **Token lifetime:** the Domino JWT TTL is minutes. ACT re-reads it per request, so there is no refresh and no Snowflake session to keep.
- **What ACT stores:** account host, user name `ACT_SVC`, role, warehouse, setup nonce. **No secret.**
- **Redirect constraints:** none. The finish link is a plain link back to ACT; nothing redirects.
- **Generated worksheet** (ACT fills `<…>` from its own token claims, its App URL and a nonce):
```sql
-- ACT (Agent Control Tower) read-only setup. Generated 2026-11-12 for https://se-demo.domino.tech/apps/<app-id>/
-- Run as ACCOUNTADMIN with "Run All". Idempotent. Read-only: ACT never runs or changes agents.
USE ROLE ACCOUNTADMIN;
CREATE ROLE IF NOT EXISTS ACT_READER COMMENT = 'ACT read-only inventory. setup:<nonce>';
CREATE WAREHOUSE IF NOT EXISTS ACT_WH WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
GRANT USAGE ON WAREHOUSE ACT_WH TO ROLE ACT_READER;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER    TO ROLE ACT_READER;   -- required by GET_AI_OBSERVABILITY_EVENTS
GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER   TO ROLE ACT_READER;   -- CORTEX_AGENT_USAGE_HISTORY
GRANT DATABASE ROLE SNOWFLAKE.SECURITY_VIEWER TO ROLE ACT_READER;  -- GRANTS_TO_ROLES / GRANTS_TO_USERS
GRANT APPLICATION ROLE SNOWFLAKE.AI_OBSERVABILITY_READER TO ROLE ACT_READER;
-- GRANT READ UNREDACTED AI OBSERVABILITY EVENTS TABLE ON ACCOUNT TO ROLE ACT_READER;  -- opt-in: prompts/responses
-- MONITOR (read spec, versions, traces; cannot invoke) on every schema that has agents today, plus future agents.
EXECUTE IMMEDIATE $$
DECLARE c CURSOR FOR SELECT DISTINCT "database_name" AS db, "schema_name" AS sch
                     FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())) WHERE "database_name" NOT LIKE 'USER$%';
BEGIN
  SHOW AGENTS IN ACCOUNT;
  FOR r IN c DO
    EXECUTE IMMEDIATE 'GRANT USAGE ON DATABASE "' || r.db || '" TO ROLE ACT_READER';
    EXECUTE IMMEDIATE 'GRANT USAGE ON SCHEMA "' || r.db || '"."' || r.sch || '" TO ROLE ACT_READER';
    EXECUTE IMMEDIATE 'GRANT MONITOR ON ALL AGENTS IN SCHEMA "' || r.db || '"."' || r.sch || '" TO ROLE ACT_READER';
    EXECUTE IMMEDIATE 'GRANT MONITOR ON FUTURE AGENTS IN SCHEMA "' || r.db || '"."' || r.sch || '" TO ROLE ACT_READER';
  END FOR;
  RETURN 'ok';
END;
$$;
CREATE OR REPLACE USER ACT_SVC TYPE = SERVICE DEFAULT_ROLE = ACT_READER DEFAULT_WAREHOUSE = ACT_WH
  WORKLOAD_IDENTITY = (TYPE = OIDC
                       ISSUER = 'https://se-demo.domino.tech/auth/realms/DominoRealm'
                       SUBJECT = '<sub of Domino service account act-svc>'
                       OIDC_AUDIENCE_LIST = ('<aud from ACT token>'))
  COMMENT = 'ACT (Domino) WIF reader. setup:<nonce>';
GRANT ROLE ACT_READER TO USER ACT_SVC;
-- If your account has a network policy, also allow Domino egress IPs for ACT_SVC (ACT shows them).
SELECT 'https://se-demo.domino.tech/apps/<app-id>/#/connect/snowflake/finish?host='
       || LOWER(v.value:host::string) || '&org=' || CURRENT_ORGANIZATION_NAME()
       || '&account=' || CURRENT_ACCOUNT_NAME() || '&nonce=<nonce>' AS "Finish in ACT (click or copy)"
FROM TABLE(FLATTEN(PARSE_JSON(SYSTEM$ALLOWLIST()))) v
WHERE v.value:type::string = 'SNOWFLAKE_DEPLOYMENT_REGIONLESS';
```
  - **Verify live:** the RESULT_SCAN-inside-cursor pattern; `GRANT MONITOR ON ALL AGENTS IN SCHEMA` (seen in Openlayer's guide, not in Snowflake's reference); `FUTURE AGENTS IN DATABASE` (would cover new schemas, unverified); the application role name `AI_OBSERVABILITY_READER`.
  - The scripting block re-runs from the ACT "Re-grant" button when usage history shows uncovered schemas.
- **Auto-verification** (on the finish link):
  1. Decode the claims locally. Call `SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME()`.
  2. Run `SHOW ROLES LIKE 'ACT_READER'` and check that the comment contains the nonce. This binds the link to this wizard session.
  3. Run `SHOW AGENTS IN ACCOUNT ->>` count.
  4. Run a one-row probe of `CORTEX_AGENT_USAGE_HISTORY`.
  5. Run `GET_AI_OBSERVABILITY_EVENTS` on one agent with `LIMIT 1`.
  6. Run a `GRANTS_TO_ROLES` probe.
  Each check shows ok or a one-line fix.

### B. Snowflake OAuth, custom PUBLIC client + PKCE, popup (FALLBACK)
Use this when the bank forbids service users or external IdP trust, or when Domino's issuer isn't publicly reachable.
- **Admin SQL** (same worksheet pattern; roles block as above, minus the user):
```sql
CREATE SECURITY INTEGRATION IF NOT EXISTS ACT_OAUTH TYPE = OAUTH OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'PUBLIC' OAUTH_ENFORCE_PKCE = TRUE
  OAUTH_REDIRECT_URI = 'https://se-demo.domino.tech/apps/<app-id>/api/connections/snowflake/oauth/callback'
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE OAUTH_REFRESH_TOKEN_VALIDITY = 7776000
  ALLOWED_ROLES_LIST = ('ACT_READER') ENABLED = TRUE;
GRANT ROLE ACT_READER TO USER <admin or a designated reviewer>;
-- final row: finish link that also carries client_id
SELECT PARSE_JSON(SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('ACT_OAUTH')):OAUTH_CLIENT_ID::string;
```
- **Flow:**
  1. ACT opens a popup at `https://{host}/oauth/authorize?client_id=…&response_type=code&redirect_uri=…&scope=session:role:ACT_READER%20refresh_token&state=…&code_challenge=…&code_challenge_method=S256`.
  2. The callback exchanges the code at `POST https://{host}/oauth/token-request`.
  3. ACT sends `postMessage` to the opener and closes the popup.
- **Proxy constraints:** the redirect must match **exactly**, use TLS, and contain no query string. Build it from the public host plus `DOMINO_RUN_HOST_PATH`, never from `localhost`. Add a vanity App URL via `OAUTH_ALTERNATE_REDIRECT_URIS`. The popup carries the Domino session cookie, so the callback passes Domino auth.
- **Lifetimes:** access token 600 s by default (60–3600); refresh token up to 90 days. After that the user re-consents ("Reconnect" banner). ACCOUNTADMIN, SECURITYADMIN and ORGADMIN are blocked by default, which is fine here. Syncs act **as that user**.
- **ACT stores:** the refresh token, encrypted. That is a long-lived secret.
- **Verify:** that PUBLIC clients get refresh tokens.
- **Downsides:** an admin still runs SQL, there is a 90-day re-consent, and it is tied to a person.

### C. Key pair, ACT-generated (LAST RESORT, still zero typing)
- ACT generates RSA-2048, embeds `ALTER USER ACT_SVC SET RSA_PUBLIC_KEY='MIIB…'` in the same worksheet (the user has no WORKLOAD_IDENTITY), and keeps the private key encrypted.
- JWT: `iss = {ORG-ACCOUNT}.ACT_SVC.SHA256:{fp}`, `sub = {ORG-ACCOUNT}.ACT_SVC`, max 1 h, with token type `KEYPAIR_JWT`. Rotate through `RSA_PUBLIC_KEY_2`.
- The UX is identical to A, but ACT holds a long-lived secret.
- **Not offered:** PATs (a pasted secret, and service users need a network policy by default) and passwords.

**Recommendation:** use **A** as the primary and **B** as the fallback. Keep C for Domino deployments whose issuer is private.

---

## 5. Auto-discovery of scopes (no typing)
1. The account comes from the finish link (`host`, `org`, `account`).
2. `SHOW AGENTS IN ACCOUNT` gives the (database, schema) groups with counts. These are the scopes, all preselected.
3. `SHOW EXTERNAL AGENTS IN ACCOUNT` and `SHOW MCP SERVERS IN ACCOUNT` give the counts shown alongside.
4. Gap check:
   ```sql
   SELECT DISTINCT agent_database_name, agent_schema_name, agent_name
   FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY
   WHERE start_time > DATEADD(day,-90,CURRENT_TIMESTAMP())
   ```
   Any agent here that ACT can't see is shown as "3 agents in 2 schemas are used but ACT can't read them. Re-run grants." with a one-click regenerated grant snippet.
5. Multi-account orgs: if the worksheet ran in the organization account, `ORGANIZATION_USAGE.CORTEX_AGENT_USAGE_HISTORY` (up to 24 h latency) lists other accounts with agents. Each account still needs its own worksheet run, offered as "Connect 4 more accounts".
- **Account identifier:** it **cannot be fully avoided**. Snowflake has no global discovery API; Snowsight login itself asks for the account. The worksheet's finish link removes the typing. The fallback is one field, "Paste anything from your Snowsight address bar or account URL", parsed as:
  - `app.snowflake.com/{org}/{account}/…`
  - `app.snowflake.com/{region}/{locator}/…` (gives `{locator}.{region}.snowflakecomputing.com`)
  - `{org}-{account}[.privatelink].snowflakecomputing.com`
  - `ORG.ACCOUNT`
  - `ORG-ACCOUNT`

  ACT then normalizes it with `CURRENT_ORGANIZATION_NAME()` and `CURRENT_ACCOUNT_NAME()` after connecting.

---

## 6. Wizard steps and failures
1. **Gallery:** click the "Snowflake Cortex Agents" card (badges: Secretless, Read-only).
2. **Connect:** an explainer ("ACT creates a read-only role and a service user that trusts this Domino deployment; no passwords") with **Copy setup SQL**, **Open Snowsight**, **Send to admin**, and an advanced toggle for OAuth or key pair. ACT shows "Waiting for you to finish in Snowflake…".
3. **Finish link:** the click lands on `#/connect/snowflake/finish` and verification starts automatically. A live checklist shows: signed in as ACT_SVC; role ACT_READER; N agents visible; usage history; traces (redacted or full); grants view.
4. **Scopes:** database and schema groups with counts, all selected, plus the gap warning.
5. **Discover** (Domino Job), then **Review** and **Import** as in the plan.

User actions: **3–4 clicks in ACT** plus paste and Run All in Snowsight. **Typed fields: 0.**

| Where | Error (code / HTTP) | Plain-language fix |
|---|---|---|
| Worksheet | `003001` / 42501 insufficient privileges | "Run this as ACCOUNTADMIN (first line `USE ROLE ACCOUNTADMIN`), or ask your Snowflake admin; use Send to admin." |
| Worksheet | `099706` duplicate issuer+subject | "Another Snowflake user already trusts ACT. Drop it or re-run; the script replaces ACT_SVC." |
| Worksheet | `002003` object does not exist or not authorized (grant on an agent schema) | "Schema dropped or managed-access; ACT skipped it." (Wrap in an EXCEPTION block.) |
| Verify | HTTP 401, WIF rejected (code to be recorded) | "Snowflake didn't accept ACT's Domino identity. The App must run as service account act-svc; re-generate the SQL." (Show iss/sub/aud diffs.) |
| Verify | HTTP 401, issuer keys unreachable | "Snowflake can't reach Domino's sign-in keys (private network). Use Key pair." |
| Verify | `390422` IP not allowed (network policy) | "Your account restricts IPs. Add Domino egress IPs <list> to a network policy on ACT_SVC." |
| Verify | `390189` role not granted | "Re-run the SQL; ACT_READER wasn't granted to ACT_SVC." |
| Verify | nonce mismatch (ACT) | "This link is from an older setup. Copy the SQL again." |
| Inventory | 0 agents, but usage history > 0 | "ACT can't see your agents yet. Run the grant snippet (re-generated)." |
| Inventory | `agent_spec` NULL (`is_secure`) | "Secure agent: tools hidden from non-owners. Ask the owner to grant OWNERSHIP… or accept a partial record; ACT infers tools from traces." |
| Traces | `000606` no active warehouse | "ACT_WH missing or suspended without AUTO_RESUME; re-run SQL." |
| Traces | rows without content | "Prompts are redacted. Optional: grant READ UNREDACTED AI OBSERVABILITY EVENTS TABLE." |
| OAuth | `invalid_redirect_uri` / redirect mismatch | "The App URL changed. ACT re-generates the integration SQL with the new URL." |
| OAuth | `invalid_scope` | "Your user lacks ACT_READER: `GRANT ROLE ACT_READER TO USER <you>`." |
| OAuth | `390318` access token expired / refresh `invalid_grant` | Silent refresh; when the refresh token expires (90 d): "Reconnect Snowflake." |
| OAuth | `390303` invalid OAuth token | Reconnect |
| Any | HTTP 429 / `000625` | Back off and retry, then continue other scopes |

(Error numbers other than `099706` come from general Snowflake knowledge. Confirm each with a recorded fixture.)

---

## 7. Intake of one agent (extra calls)
1. `DESCRIBE AGENT db.s.a`: spec, then tools, models and MCP.
2. `SHOW VERSIONS IN AGENT db.s.a`: versions and default.
3. `SHOW GRANTS ON AGENT db.s.a`: invokers (watch for USAGE granted to PUBLIC).
4. For each tool identifier, best effort: `DESCRIBE SEMANTIC VIEW`, `DESCRIBE CORTEX SEARCH SERVICE`, `DESCRIBE PROCEDURE` (`EXECUTE AS`), `DESCRIBE EXTERNAL MCP SERVER` (URL only).
5. `GRANTS_TO_ROLES` filtered to those objects: who else can read the underlying data.
6. `CORTEX_AGENT_USAGE_HISTORY` for this agent over 30 d: callers, volume, credits, roles used.
7. `GET_AI_OBSERVABILITY_EVENTS(…)` since the last sync: runs for `list_runs` (trace_id, start, error severity).
8. Optional: `SYSTEM$GET_TAG` for owner and cost center; `QUERY_HISTORY` for the CREATE AGENT user.

---

## 8. Recorded-HTTP fixtures (contract tests), under `fixtures/http/snowflake/`
1. `sqlapi_show_agents_account_200.json`, plus `…_partitioned_200.json` + `…_partition_1.json`, `…_async_202.json` + `statement_status_200.json`
2. `sqlapi_show_agents_empty_200.json` (grant gap), `sqlapi_show_agents_10000_cap.json`
3. `sqlapi_describe_agent_200.json`, `…_secure_null_spec.json`, `…_mcp_and_generic_tools.json`
4. `sqlapi_show_versions_in_agent_200.json`, `sqlapi_show_grants_on_agent_200.json` (including PUBLIC)
5. `sqlapi_grants_to_roles_200.json`, `sqlapi_cortex_agent_usage_history_200.json`
6. `sqlapi_ai_observability_events_redacted_200.json`, `…_unredacted_200.json`, `…_no_warehouse_000606.json`
7. `sqlapi_show_mcp_servers_200.json`, `sqlapi_show_external_agents_200.json`, `sqlapi_describe_external_mcp_200.json`
8. `rest_list_agents_200.json` (with `Link` header), `rest_get_agent_200.json`
9. Auth: `wif_ok_current_user.json`, `wif_401_subject_mismatch.json`, `wif_401_audience.json`, `wif_401_jwks_unreachable.json`, `network_policy_390422.json`, `role_not_granted_390189.json`
10. OAuth: `oauth_token_request_200.json`, `oauth_refresh_200.json`, `oauth_invalid_grant_400.json`, `oauth_expired_390318.json`
11. `sqlapi_insufficient_privileges_003001.json`, `sqlapi_does_not_exist_002003.json`, `sqlapi_429.json`
12. `sqlapi_show_roles_act_reader_nonce.json` (verification)

---

## 9. Risks, unknowns, sources

**Top risks**
1. **The Domino JWT as a Snowflake WIF token (high).**
   - Unknowns: whether the token has an `aud` Snowflake can match (Keycloak access tokens may carry `account` or several audiences, and Snowflake requires each to be in the list for some providers), `iat` and RS256 support (likely), and whether the App and the crawl Jobs both carry the **service account's** `sub`.
   - If ACT is published by a person, `sub` is that person and breaks on republish.
   - Mitigation: decode the claims in the wizard (never display the token), and run a "WIF self-test" before generating SQL.
2. **Silent, grant-gated visibility (high).**
   - SHOW returns only granted objects, so new schemas, personal-DB agents and Native App agents are missed.
   - Secure agents hide the spec.
   - Mitigation: the usage-history gap check and the Re-grant button. Verify `FUTURE AGENTS IN DATABASE`.
3. **The data scope depends on the caller (medium-high).**
   - Tools run under the invoking user's roles, so the agent object alone cannot say what data it touches. Owner's-rights procedures escalate.
   - ACT must show "tool objects + who can invoke + per-run roles from usage history", and the tiering rules must treat USAGE granted to PUBLIC as org-wide exposure.

**Other unknowns:**
- No documented way to list the agents in the Snowflake Intelligence object.
- `SHOW VERSIONS IN AGENT` column names.
- Whether Snowsight renders the finish URL as a clickable link (otherwise the user copies the cell; still nothing typed).
- The Snowsight deep-link path for an agent.
- Whether PUBLIC OAuth clients get refresh tokens.
- Exact WIF error codes.
- GA labels for External Agents, versioning and MCP connectors.
- Bank network policies may block Domino egress.
- No `last_altered` on agents.

**Sources (all accessed 2026-11-12)**
- CREATE AGENT: https://docs.snowflake.com/en/sql-reference/sql/create-agent
- SHOW AGENTS: https://docs.snowflake.com/en/sql-reference/sql/show-agents
- DESCRIBE AGENT: https://docs.snowflake.com/en/sql-reference/sql/desc-agent
- Cortex Agents overview: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents
- Agents access control: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-setup
- Agents REST API (objects): https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-rest-api
- Agents Run API (spec fields): https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-run
- Threads: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-threads
- Versioning: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-versioning
- Monitor agent requests: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-monitor
- GET_AI_OBSERVABILITY_EVENTS: https://docs.snowflake.com/sql-reference/functions/get_ai_observability_events-snowflake-local
- CORTEX_AGENT_USAGE_HISTORY: https://docs.snowflake.com/en/sql-reference/account-usage/cortex_agent_usage_history
- Account Usage roles: https://docs.snowflake.com/en/sql-reference/account-usage
- SHOW GRANTS: https://docs.snowflake.com/en/sql-reference/sql/show-grants
- Managed MCP server: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp
- CREATE MCP SERVER: https://docs.snowflake.com/en/sql-reference/sql/create-mcp-server
- MCP connectors: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp-connectors
- External agents: https://docs.snowflake.com/en/sql-reference/sql/alter-external-agent
- Snowflake Intelligence / CoWork agents: https://docs.snowflake.com/en/user-guide/snowflake-cortex/snowflake-cowork/deploy-agents
- Workload identity federation: https://docs.snowflake.com/en/user-guide/workload-identity-federation
- REST API auth (WIF header): https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/authentication
- SQL API auth: https://docs.snowflake.com/en/developer-guide/sql-api/authenticating
- OAuth integration syntax: https://docs.snowflake.com/en/sql-reference/sql/create-security-integration-oauth-snowflake
- OAuth custom clients: https://docs.snowflake.com/en/user-guide/oauth-custom
- Account identifiers: https://docs.snowflake.com/en/user-guide/admin-account-identifier
- Release notes 2026: https://docs.snowflake.com/en/release-notes/new-features-2026
- Cortex Agents GA: https://docs.snowflake.com/en/release-notes/2025/other/2025-11-04-cortex-agents
- Snowflake Intelligence GA: https://docs.snowflake.com/release-notes/2025/other/2025-11-04-snowflake-intelligence
- Object enhancements GA: https://docs.snowflake.com/en/release-notes/2026/other/2026-09-16-cortex-agents-object-enhancements-ga
- Third-party read-only integration pattern (grants, span names): https://docs.openlayer.com/integrations/snowflake-cortex
- Authentication policy (OIDC issuers, secondary): https://medium.com/snowflake/the-end-of-secrets-how-workload-identity-federation-is-rewriting-snowflake-authentication-22b9ed837eb6
