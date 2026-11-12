# Provider research: AWS (Bedrock AgentCore, Agent Registry, classic Bedrock Agents)

Researched 2026-11-12. Research only, no app code. Sources are in section 9, all accessed 2026-11-12.

**Bottom line.**
- **Auth:** one CloudFormation quick-create link (a StackSet for an org) creates a read-only role. The role trusts this Domino deployment's Keycloak issuer through `AssumeRoleWithWebIdentity`, so nothing secret is stored.
- **Inventory:** the `bedrock-agentcore-control` client, plus the new `agent-registry-control` client (and the `bedrock-agent` client for classic agents).
- **Gap in today's code:** today's fixtures and adapter use shapes that AWS has retired (see section 9, risk 1).

---

## 1. What counts as an "agent" on AWS (late 2026)

| Product / resource | Resource type, ARN | Agent for ACT? | Status |
|---|---|---|---|
| **AgentCore Runtime** (agent runtime) | `AWS::BedrockAgentCore::Runtime`, `arn:aws:bedrock-agentcore:{r}:{acct}:runtime/{name}-{10char}` | **Yes, primary.** Any framework (Strands, LangGraph, CrewAI…) hosted as a container or code. Protocols: HTTP, MCP, A2A, AGUI. | GA (AgentCore GA Oct 2025; Instances compute Aug 2026; V2 platform Nov 2026) |
| **AgentCore harness** | `arn:…:harness/{name}-{10char}` | **Yes.** A managed, config-defined agent with model, tools, skills, system prompt and memory. It is the richest record AWS offers. It is backed by a managed Runtime. | Preview Apr 2026, **GA Jun 2026** |
| AgentCore Runtime endpoint | `…:runtime/{id}/runtime-endpoint/{name}` | A version alias (`DEFAULT` plus up to 10). ACT stores it on the agent record. | GA |
| **AgentCore Gateway** + targets | `AWS::BedrockAgentCore::Gateway`, `…:gateway/{id}` | **Tool surface (MCP server), not an agent.** Map it to `dependencies.mcp_servers` and tools. Targets cover Lambda, OpenAPI, Smithy, MCP server, API Gateway, Runtime, Step Functions, HTTP passthrough and inference. | GA |
| AgentCore Identity | workload identity, OAuth2 and API-key credential providers | Agent identity and egress credentials. Map to `identity` and `systems`. | GA |
| AgentCore Memory | `…:memory/{id}` | A data store an agent uses. Map to `data_scopes`. | GA |
| AgentCore Policy | policy engine (Cedar) attached to a Gateway | Guardrail evidence. Map to `criteria` and `extra`. | GA Mar 2026 |
| AgentCore Observability, Evaluations | CloudWatch / X-Ray / OTel | Runs, traces, quality | GA |
| Browser, Code Interpreter, Web Search, Payments | built-in tools | Tools | GA (Payments in **preview**) |
| **AWS Agent Registry** | `arn:aws:agent-registry:{r}:{acct}:registry/{id}[/record/{rid}]` | **Catalog.** Records of type `AGENT`, `MCP`, `SKILL`, `CUSTOM`; gateways auto-detected as `GATEWAY`. Can **auto-detect every Runtime and Gateway across an AWS Organization.** | Preview Apr 2026, **GA Aug 2026**, 9 Regions plus GovCloud West. **Moved to its own `agent-registry` namespace on 2026-08-06. The old `bedrock-agentcore` registry API shut down 2026-10-30.** |
| **Classic Bedrock Agents** | `arn:aws:bedrock:{r}:{acct}:agent/{10char}` | **Yes.** Agent plus versions, aliases, action groups (Lambda / function schema), knowledge bases, collaborators (multi-agent) and a guardrail. | GA (legacy, still widely deployed) |

Banks will have Runtimes, harnesses and classic agents side by side, so ACT should list all three as agents. Gateways, Memory, Identity and Policy enrich those records.

---

## 2. Inventory APIs

### 2.1 SDK clients (boto3)
| Client | Endpoint | Used for |
|---|---|---|
| `bedrock-agentcore-control` | `bedrock-agentcore-control.{r}.amazonaws.com` | Runtime, harness, gateway, memory, identity, policy (control plane) |
| `agent-registry-control` | `agent-registry-control.{r}.api.aws` | Registries, all records including drafts, and provenance |
| `agent-registry` | `agent-registry.{r}.api.aws` | Approved-record discovery (List/Search/BatchGet), MCP endpoint |
| `bedrock-agent` | `bedrock-agent.{r}.amazonaws.com` | Classic Bedrock Agents |
| `iam`, `sts`, `organizations`, `account`, `logs`, `cloudwatch`, `xray`, `cloudtrail` | — | Role policies, identity, scopes, runs, creator |

All operations need a recent botocore; harness, registry and Instances shapes date from 2026. Pin the botocore version and add a guard for a missing client (`UnknownServiceError`).

### 2.2 AgentCore Runtime
- **`ListAgentRuntimes`** (`POST /runtimes/?maxResults=1..100&nextToken=`; paginator `list_agent_runtimes`)
  - Returns `agentRuntimes[]` with `agentRuntimeArn`, `agentRuntimeId`, `agentRuntimeName`, `agentRuntimeVersion`, `description`, `lastUpdatedAt`, `status`.
  - It does **not** return tags, role, `createdAt` or protocol.
- **`GetAgentRuntime`** (`GET /runtimes/{agentRuntimeId}/?version=N`) returns:
  - Identity, version and description: `agentRuntimeArn`, `agentRuntimeId`, `agentRuntimeName`, `agentRuntimeVersion`, `description`.
  - Status and timestamps: `status` (`CREATING|CREATE_FAILED|UPDATING|UPDATE_FAILED|READY|DELETING|DELETE_FAILED`), `failureReason`, `createdAt`, `lastUpdatedAt`.
  - **`roleArn`** (execution role, the source of data scopes) and **`workloadIdentityDetails.workloadIdentityArn`**.
  - `authorizerConfiguration` (`customJWTAuthorizer.discoveryUrl`, `allowedClients`, `allowedAudience`) and `protocolConfiguration.serverProtocol`.
  - `agentRuntimeArtifact` (container URI or code), `networkConfiguration.networkMode` (`PUBLIC` or `VPC`, plus subnets and security groups).
  - **`environmentVariables`**, which often hold `MODEL_ID`, `GATEWAY_URL` and `MEMORY_ID` (and sometimes secrets; see section 7).
  - `lifecycleConfiguration`, `filesystemConfigurations`, `capacityProviderConfiguration`, `platformVersion`.
- **`ListAgentRuntimeVersions`** (`agentRuntimeId`): same summary shape, one item per version.
- **`ListAgentRuntimeEndpoints`** (`agentRuntimeId`) returns `runtimeEndpoints[]` with `name`, `liveVersion`, `targetVersion`, `status`, `agentRuntimeEndpointArn`, `createdAt`, `lastUpdatedAt`.
- **`ListTagsForResource`** (`resourceArn`) returns `{ "tags": { "owner": "…" } }`.
- **Rate limits (fixed, per account per Region):** the List APIs share **25 TPS** (ListAgentRuntimes, Endpoints, Versions). The Get APIs share **150 TPS**.

```json
{"agentRuntimes":[{"agentRuntimeArn":"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/treasury_sweep-Xk3Lq9ZpT2",
 "agentRuntimeId":"treasury_sweep-Xk3Lq9ZpT2","agentRuntimeName":"treasury_sweep","agentRuntimeVersion":"3",
 "description":"Sweeps excess cash…","lastUpdatedAt":"2026-11-03T14:22:41Z","status":"READY"}],"nextToken":"eyJ…"}
```

### 2.3 AgentCore harness
- **`ListHarnesses`** (summaries, paginated; verify the exact summary fields against a recording) and `ListHarnessVersions`, `ListHarnessEndpoints`.
- **`GetHarness`** (`GET /harnesses/{harnessId}?harnessVersion=`) returns a `harness` object with:
  - Identity: `arn`, `harnessId`, `harnessName`, `harnessVersion`, `status`, `createdAt`, `updatedAt`.
  - **`executionRoleArn`**.
  - **`model.bedrockModelConfig.modelId`**, or the `openAiModelConfig`, `geminiModelConfig` or `liteLlmModelConfig` members.
  - Tools: **`tools[] {name,type,config}`** and **`allowedTools[]`**.
  - Behaviour: `skills[]`, `systemPrompt[]`, `memory`, `hooks[]`, `maxIterations`, `maxTokens`, `timeoutSeconds`, `environmentVariables`, `authorizerConfiguration`.

The harness is the only AgentCore resource that states its model and tools as plain configuration. For a Runtime, ACT has to infer them (see section 7).

### 2.4 Gateway and targets
- **`ListGateways`** returns `items[]` with `gatewayId`, `name`, `status`, `description`, `authorizerType`, `protocolType`, `createdAt`, `updatedAt`, plus `nextToken`.
- **`GetGateway`** adds `gatewayArn`, `gatewayUrl`, `roleArn`, `authorizerConfiguration`, `policyEngineConfiguration` (Cedar engine ARN and mode), `workloadIdentityDetails` and `kmsKeyArn`.
- **`ListGatewayTargets`** (`gatewayIdentifier`) returns `items[]` with `targetId`, `name`, `status`, `description`, `createdAt`, `updatedAt`.
- **`GetGatewayTarget`** returns:
  - **`targetConfiguration.mcp.{lambda{lambdaArn,toolSchema.inlinePayload[{name,description,inputSchema}]}|openApiSchema|smithyModel|mcpServer{endpoint}|apiGateway|…}`**. The tool names come from this tool schema.
  - `credentialProviderConfigurations[]` (`GATEWAY_IAM_ROLE|OAUTH|API_KEY` plus the provider ARN), `protocolType`, `status`, `statusReasons`, `lastSynchronizedAt`.
- **Rate limits:** List and Get are 10 TPS each.
- Do **not** call the Gateway's MCP `tools/list`. That is a data-plane invoke and needs the Gateway's own inbound authentication.

### 2.5 Identity, Memory, Policy (enrichment)
- **`ListWorkloadIdentities`** returns `workloadIdentities[] {name, workloadIdentityArn}`. **`GetWorkloadIdentity`** returns `allowedResourceOauth2ReturnUrls`, `createdTime` and `lastUpdatedTime`. Limit: 20 TPS.
- **`ListOauth2CredentialProviders`** and **`ListApiKeyCredentialProviders`** return `credentialProviders[] {name, credentialProviderVendor, credentialProviderArn, createdTime, lastUpdatedTime}`. The vendor names the external system (for example Salesforce or Slack), so it maps to `systems`.
- **`ListMemories`** (5 TPS) returns `memories[] {arn,id,status,createdAt,updatedAt}`. **`GetMemory`** (50 TPS) returns `name`, `strategies[]`, `memoryExecutionRoleArn` and `eventExpiryDuration`.
- **`ListPolicyEngines`**, **`ListPolicies`** (5 TPS): evidence that guardrails exist.

### 2.6 AWS Agent Registry (catalog, organization-wide shortcut)
- **`ListRegistries`** (`agent-registry-control`, `POST /registries-list`, `filters[]`, `maxResults`, `nextToken`) returns `registries[]`, including `autoDetection.status` (`ACTIVE|INACTIVE`) and `discoveryConfiguration`.
- **`ListRegistryRecords`** (`POST /registries/{registryId}/records-list`, `filters:[{name:"recordType",values:["AGENT"]}]`) lists **all** records, including drafts. This needs control-plane rights in the owner account, or the RAM `ReadOnly` share.
- **`GetRegistryRecord`** returns `recordId`, `recordArn`, `name`, `displayName`, `description`, `recordType`, `recordVersion`, `status` (`DRAFT|PENDING_APPROVAL|APPROVED|REJECTED|DEPRECATED`), `createdAt`, `updatedAt`, `descriptors.{a2aAgentCard|mcpServer|agentSkillsDefinition|custom}.data` (the agent card or MCP JSON as a string; MCP tools are under `mcpServer.additionalData.tools`) and **`provenance[] {relation:"DETECTED_FROM", sourceId:<runtime/gateway ARN>, sourceType, sourceDetails}`**.
- Data plane (approved records only):
  - `ListDiscoverableRegistryRecords` (`POST /registries/{id}/discoverable-records-list`)
  - `BatchGetDiscoverableRegistryRecord` (`POST /discoverable-records-batch`, one registry with 1 to 100 IDs; returns `registryRecords[]` and `errors[] {errorCode: RESOURCE_NOT_FOUND|ACCESS_DENIED|INTERNAL_ERROR}`)
  - `SearchDiscoverableRegistryRecords`
- **Rate limits:** 5 to 10 TPS.
- **Organization auto-detection:** one organization-scoped registry per Region, in the management or delegated-admin account. It catalogs every member account's Runtimes and Gateways as `aws-autodetected-<acct>-<region>-<resourceId>` records and keeps them in sync, including as accounts join or leave. **When it is ACTIVE, ACT reads one registry per Region and gets an organization-wide agent list for free.** It still needs per-account `GetAgentRuntime` calls for role, model and tools.

```json
{"recordId":"rec-xk3lq9zpt2","name":"aws-autodetected-123456789012-us-east-1-treasury_sweep-Xk3Lq9ZpT2",
 "displayName":"treasury-sweep","recordType":"AGENT","recordVersion":"3","status":"APPROVED",
 "descriptors":{"http":{"data":"…"}},"provenance":[{"relation":"DETECTED_FROM",
 "sourceId":"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/treasury_sweep-Xk3Lq9ZpT2",
 "sourceType":"AWS::BedrockAgentCore::Runtime","sourceDetails":{"agentcoreRuntime":{"protocolConfiguration":{"serverProtocol":"HTTP"},
 "workloadIdentityDetails":{"workloadIdentityArn":"arn:…:workload-identity-directory/default/workload-identity/treasury_sweep-…"}}}}],
 "createdAt":"2026-09-01T10:00:00Z","updatedAt":"2026-11-03T14:22:41Z"}
```

The auto-detected runtime descriptor key is listed as `http`, `mcpServer`, `a2aAgentCard` or `agui`. The exact wire key for `http` and `agui` needs checking against a recording.

### 2.7 Classic Bedrock Agents (`bedrock-agent`)
- **`ListAgents`** (`maxResults`, `nextToken`) returns `agentSummaries[] {agentId, agentName, agentStatus, description, latestAgentVersion, updatedAt, guardrailConfiguration}`.
- **`GetAgent`** returns `agent`:
  - Identity and status: `agentArn`, `agentId`, `agentName`, `agentVersion` (`DRAFT`), `agentStatus`, `failureReasons`, `recommendedActions`.
  - **`agentResourceRoleArn`**, **`foundationModel`**, `instruction`.
  - `guardrailConfiguration {guardrailIdentifier, guardrailVersion}`, `memoryConfiguration`, `agentCollaboration` (`SUPERVISOR|SUPERVISOR_ROUTER|DISABLED`), `orchestrationType`.
  - Timestamps: `createdAt`, `updatedAt`, `preparedAt`.
- **`ListAgentVersions`** and **`ListAgentAliases`** (`agentAliasSummaries[] {agentAliasId, agentAliasName, routingConfiguration[{agentVersion}], agentAliasStatus, createdAt, updatedAt}`).
- **`ListAgentActionGroups(agentId, agentVersion)`**, then **`GetAgentActionGroup`** returns `actionGroupExecutor.lambda`, `apiSchema` and `functionSchema.functions[] {name, parameters, requireConfirmation}`. **`requireConfirmation` is direct human-in-the-loop evidence.**
- **`ListAgentKnowledgeBases`** (maps to data scopes) and **`ListAgentCollaborators`** (maps to multi-agent dependencies). `ListTagsForResource(resourceArn)` returns tags.
- **Rate limits:** not published per operation. Treat them as low (about 5 to 10 TPS), back off on `ThrottlingException`, and check Service Quotas.

### 2.8 Execution role, so ACT can derive data scopes
- Calls: `iam:GetRole` (trust policy, `MaxSessionDuration`, tags), `ListAttachedRolePolicies`, `ListRolePolicies`, `GetRolePolicy`, `GetPolicy`, `GetPolicyVersion` (on the default version).
- Parse the statements: `Resource` ARNs become `data_scopes`, for example `s3:::bucket/prefix`, `dynamodb:table/x` and `secretsmanager:secret:x`.
- The resources on `bedrock:InvokeModel` also give **model IDs**.
- Write actions such as `s3:PutObject`, `dynamodb:PutItem` and `lambda:InvokeFunction` feed the consequence score.
- IAM is global and limited to about 20 TPS for reads, so cache by role ARN.

### 2.9 Runs, traces, observability (CloudWatch, OTel, X-Ray)
- **Metrics:** namespace `AWS/Bedrock-AgentCore`. Metrics: `Invocations`, `SessionCount`, `ActiveSessionCount`, `Latency`, `OverheadLatency`, `SystemErrors`, `UserErrors`, `Throttles`, `CPUUsed-vCPUHours`, `MemoryUsed-GBHours`. Dimensions: `Service=AgentCore.Runtime`, `Resource=<agent ARN>`, `Name=<Agent>::<Endpoint>`. API: `cloudwatch:GetMetricData`.
- **Spans:** in the log group `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint>`, or `aws/spans` for agents on the shared destination (Transaction Search must be enabled). Span attributes:
  - `aws.resource.arn`, `aws.agent.id`, `aws.endpoint.name`, `session.id`, `latency_ms`, `error_type`
  - `aws.agentcore.runtime.version`, from 2026-11-16
  - Plus framework OTel GenAI spans (ADOT) carrying `gen_ai.request.model` and tool-call spans.
  - API: `logs:StartQuery` / `GetQueryResults` (Logs Insights) or `xray:GetTraceSummaries` / `BatchGetTraces`.
- `APPLICATION_LOGS` include **`request_payload`**, which is customer content. Make reading payloads opt-in.
- **SourceRun:** one row per `session.id` (with `started_at` and `status` from `error_type`), from Logs Insights `stats min(startTimeUnixNano), count(*) by attributes.session.id`.
- **Classic agents:** model invocation logging, where enabled, plus CloudWatch `AWS/Bedrock` metrics. Bedrock Agents has no run-list API.
- **Cross-account:** CloudWatch cross-account observability (OAM sink) lets a monitoring account query all accounts' spans. If the bank has one, scope ACT's trace reads there.

### 2.10 MCP servers
- **Agent Registry MCP endpoint** (`https://agent-registry.{r}.api.aws/registry/{id}/mcp`) with the tools `search_discoverable_registry_records`, `list_discoverable_registry_records` and `batch_get_discoverable_registry_record`.
  - Auth is SigV4 (`agent-registry:InvokeRegistryMcp`) or JWT.
  - It only sees **approved** records and offers nothing the REST API lacks.
  - Useful later for an "ask ACT" feature, not for discovery.
- **Managed AWS MCP Server** (GA 2026-05-06, initially us-east-1 and eu-central-1) and **awslabs/mcp** (including an AgentCore server): built for coding agents. The generic `call_aws` tool was reportedly deprecated 2026-07-15 (third-party source, unverified).
- **Recommendation:** ACT should call boto3 directly. Read-only, typed and testable calls beat an LLM tool layer for a system of record.

---

## 3. Field mapping to `SourceAgent`

| SourceAgent | Runtime (`GetAgentRuntime` …) | Harness (`GetHarness`) | Classic (`GetAgent` …) | Registry record (fallback / org) |
|---|---|---|---|---|
| `platform` | `"aws"` (put `kind: runtime/harness/bedrock-agent` in `extra`) | same | same | same |
| `native_id` | `agentRuntimeArn` (an ARN is unique across accounts and Regions; the bare ID is not) | `arn` | `agentArn` | `provenance[0].sourceId`, else `recordArn` |
| `name` | `agentRuntimeName` | `harnessName` | `agentName` | `displayName` (not `name`, which is `aws-autodetected-…`) |
| `description` | `description` | — | `description` | `description` |
| `version` | `agentRuntimeVersion` | `harnessVersion` | latest alias's `routingConfiguration[0].agentVersion`, else `latestAgentVersion` | `recordVersion` |
| `owner` | tag `owner` / `Owner` / `team` / `CreatedBy`, else the CloudTrail `CreateAgentRuntime` `userIdentity.arn` (90 days only) | tags | tags | registry custom metadata, or `RecordCreatorAccount` |
| `status` | `status` | `status` | `agentStatus` (`PREPARED`, `NOT_PREPARED`, `FAILED`…) | `status` (approval state, so put it in `extra.registry_status`) |
| `last_updated` | `lastUpdatedAt` | `updatedAt` | `updatedAt` | `updatedAt` |
| `url` | console deep link `https://{r}.console.aws.amazon.com/bedrock-agentcore/home?region={r}#/agents/{id}` (format to verify) | console | console | — |
| `provider` | `"AWS Bedrock AgentCore"` | same | `"AWS Bedrock Agents"` | — |
| `tools` | linked Gateway targets' `toolSchema` names; A2A card `skills`; tool spans | `tools[].name` ∩ `allowedTools` | action groups' `functionSchema.functions[].name` / OpenAPI `operationId` | `mcpServer.additionalData.tools`, `a2aAgentCard.skills` |
| `data_scopes` | `roleArn` policy resources; Memory IDs; filesystem mounts | `executionRoleArn` policies; `memory` | `agentResourceRoleArn` policies; knowledge base IDs | — |
| `systems` | OAuth2 / API-key provider `credentialProviderVendor`; Gateway target endpoints / Lambda ARNs | tool configs | action-group Lambdas; collaborators | — |
| `value_limits` | not native: tags such as `act:limit:transfer_funds=250000USD`, or registry custom metadata | same | same | custom metadata |
| `criteria` | `networkMode`, `authorizerConfiguration`, Gateway `policyEngineConfiguration`, write actions in the role | plus `maxIterations`, `hooks` | `requireConfirmation`, guardrail, `agentCollaboration` | custom metadata |
| `identity` | `{type:"iam_role", principal: roleArn, workload_identity: workloadIdentityArn, distinct: role used by exactly one agent}` | `executionRoleArn` | `agentResourceRoleArn` | `sourceDetails.*.workloadIdentityDetails` |
| `versions` | `ListAgentRuntimeVersions` → `[{version, created: lastUpdatedAt}]`, plus endpoints' `liveVersion` | `ListHarnessVersions` | `ListAgentVersions` | `recordVersion` |
| `dependencies.models` | env vars (`*MODEL*`), role `bedrock:InvokeModel` resources, span `gen_ai.request.model` | `model.*.modelId` | `foundationModel` plus `promptOverrideConfiguration.promptConfigurations[].foundationModel` | — |
| `dependencies.mcp_servers` | env var `GATEWAY_URL` matched to `gatewayUrl`; Gateway targets of type `mcpServer` | MCP tools in `tools[]` | — | `GATEWAY` records |
| `extra` | `arn`, `account`, `region`, `protocol`, `endpoints`, `network_mode`, `registry_record`, `env_var_keys` (keys only) | `skills`, `system_prompt_hash` | `aliases`, `guardrail`, `knowledge_bases` | `provenance` |
| `raw` | redacted Get responses (env values removed) | redacted | redacted | record |

The current fixture's `metadata.{tools,data_scopes,value_limits,criteria}` **does not exist in any AWS API.** Real values come from the derivations above. Value limits and criteria come from tags or registry custom metadata, and are absent otherwise.

---

## 4. Auth options, ranked by user friction

### Option A (recommended primary): quick-create role trusting the Domino OIDC issuer (no stored secrets)
How it works:
- ACT runs inside a Domino run, which gets a per-run JWT from `http://localhost:8899/access-token`. Its issuer is `https://<host>/auth/realms/DominoRealm`.
- **Verified 2026-11-12 on se-demo:** the discovery document and JWKS are public over HTTPS (HTTP 200), `issuer` matches, and RS256 is supported. These are exactly what IAM OIDC federation requires.
- The customer account gets an IAM OIDC provider for that issuer and a read-only role whose trust policy allows `sts:AssumeRoleWithWebIdentity` with conditions on `aud` (or on `azp`, which STS uses as `aud` when present) and `sub`.
- No secret ever leaves Domino.

User steps, single account (about 6 clicks, no typing):
1. Click **Connect AWS** (ACT pre-computes the issuer, audience, subject, a nonce and its own return URL).
2. Click **Open AWS console**. A quick-create link opens in a new tab, with all parameters pre-filled. The user signs in if they are not already.
3. Tick "I acknowledge that AWS CloudFormation might create IAM resources with custom names".
4. Click **Create stack** and wait about 60 seconds.
5. Click the **Outputs** tab, then **ReturnToACT** (a URL output carrying `?role=<RoleArn>&state=<nonce>`).
6. ACT verifies everything automatically (see below). The user lands on the scopes screen.

For an AWS Organization, the admin does the same steps once, in the **management account**. The stack contains a hub role plus an `AWS::CloudFormation::StackSet` (`PermissionModel: SERVICE_MANAGED`, `AutoDeployment: Enabled`, target the root OU) that puts a spoke role in every member account, including future ones. One stack covers the whole organization.

One-time admin setup:
- None for a single account.
- For an organization: trusted access for StackSets enabled. Optionally, register a delegated admin and turn on organization-scoped Agent Registry auto-detection per Region.

What ACT must register: nothing with AWS, and no vendor app. ACT does need:
- An **S3-hosted template**, because quick-create `templateURL` must be an S3 HTTPS URL. ACT or Domino has to own a public, read-only bucket with versioned template URLs.
- Its own `sub` (for example a Domino service account or the App publisher), read from its token's claims at runtime and never logged.

Redirect and URL constraints: no OAuth redirect is registered anywhere. The return link is just a CloudFormation output value, so the App URL `/apps/<id>/` and the dev-preview `…/proxy/<port>/` both work. ACT passes `param_ReturnUrl` from its own request host plus `DOMINO_RUN_HOST_PATH`. Use a hash route (`#/connect/aws/callback`) so the Domino proxy never sees an unknown path. That the console renders URL outputs as clickable links still needs a check; the fallback is a one-field paste of `RoleArn` (and ACT accepts any ARN or console URL it can parse an account from).

Token lifetime and refresh:
- The Domino JWT is short-lived. ACT re-reads it from localhost:8899 before every `AssumeRoleWithWebIdentity`.
- STS sessions last 900 seconds up to the role's `MaxSessionDuration` (template sets 3600).
- Hub to spoke `sts:AssumeRole` is role chaining, capped at 1 hour, which is fine per crawl.
- Scheduled crawls (Domino Jobs) get their own run token. **The Job must run as the same `sub` the trust policy pins** (see risks).

What ACT stores: role ARN(s), account ID(s), the organization flag, the template version, chosen Regions, and the nonce until verified. **No secrets.**

Automatic verification (the `test_connection` contract):
1. `AssumeRoleWithWebIdentity` (`RoleSessionName=act-<connId>`), then `sts:GetCallerIdentity`.
2. Read the `act:template-version` tag on the role (`iam:GetRole` on itself).
3. In the home Region, probe each permission with `maxResults=1`: `ListAgentRuntimes`, `ListHarnesses`, `ListGateways`, `ListMemories`, `ListWorkloadIdentities`, `bedrock-agent:ListAgents`, `agent-registry-control:ListRegistries`, `iam:ListAttachedRolePolicies` (on a found role), and, if the organization flag is set, `organizations:DescribeOrganization` and `ListAccounts`.
4. Mark each permission ok or missing, with a fix ("Update the stack to template v3", with a one-click update link).

**Least-privilege read policy** (attached to the hub and spoke roles; Region conditions are optional):

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"AgentCoreRead","Effect":"Allow","Resource":"*","Action":[
  "bedrock-agentcore:ListAgentRuntimes","bedrock-agentcore:GetAgentRuntime","bedrock-agentcore:ListAgentRuntimeVersions",
  "bedrock-agentcore:ListAgentRuntimeEndpoints","bedrock-agentcore:GetAgentRuntimeEndpoint",
  "bedrock-agentcore:ListHarnesses","bedrock-agentcore:GetHarness","bedrock-agentcore:ListHarnessVersions","bedrock-agentcore:ListHarnessEndpoints",
  "bedrock-agentcore:ListGateways","bedrock-agentcore:GetGateway","bedrock-agentcore:ListGatewayTargets","bedrock-agentcore:GetGatewayTarget",
  "bedrock-agentcore:ListMemories","bedrock-agentcore:GetMemory",
  "bedrock-agentcore:ListWorkloadIdentities","bedrock-agentcore:GetWorkloadIdentity",
  "bedrock-agentcore:ListOauth2CredentialProviders","bedrock-agentcore:ListApiKeyCredentialProviders",
  "bedrock-agentcore:ListPolicyEngines","bedrock-agentcore:GetPolicyEngine","bedrock-agentcore:ListPolicies",
  "bedrock-agentcore:ListOnlineEvaluationConfigs","bedrock-agentcore:ListTagsForResource","bedrock-agentcore:GetResourcePolicy"]},
 {"Sid":"RegistryRead","Effect":"Allow","Resource":"*","Action":[
  "agent-registry:ListRegistries","agent-registry:GetRegistry","agent-registry:ListRegistryRecords","agent-registry:GetRegistryRecord",
  "agent-registry:ListDiscoverableRegistryRecords","agent-registry:GetDiscoverableRegistryRecord","agent-registry:ListTagsForResource"]},
 {"Sid":"ClassicAgentsRead","Effect":"Allow","Resource":"*","Action":[
  "bedrock:ListAgents","bedrock:GetAgent","bedrock:ListAgentVersions","bedrock:GetAgentVersion","bedrock:ListAgentAliases","bedrock:GetAgentAlias",
  "bedrock:ListAgentActionGroups","bedrock:GetAgentActionGroup","bedrock:ListAgentKnowledgeBases","bedrock:GetAgentKnowledgeBase",
  "bedrock:ListAgentCollaborators","bedrock:GetAgentCollaborator","bedrock:GetGuardrail","bedrock:ListTagsForResource"]},
 {"Sid":"RolePoliciesRead","Effect":"Allow","Resource":"*","Action":[
  "iam:GetRole","iam:ListAttachedRolePolicies","iam:ListRolePolicies","iam:GetRolePolicy","iam:GetPolicy","iam:GetPolicyVersion"]},
 {"Sid":"ScopesRead","Effect":"Allow","Resource":"*","Action":[
  "account:ListRegions","sts:GetCallerIdentity","cloudtrail:LookupEvents",
  "organizations:DescribeOrganization","organizations:ListAccounts","organizations:ListDelegatedAdministrators"]},
 {"Sid":"TracesReadOptIn","Effect":"Allow","Resource":"*","Action":[
  "cloudwatch:GetMetricData","logs:DescribeLogGroups","logs:StartQuery","logs:GetQueryResults","logs:StopQuery",
  "xray:GetTraceSummaries","xray:BatchGetTraces"]}]}
```

**Deliberately excluded** (the IAM reference rates them Read or List, but they return secrets, customer content, or invoke something):
- Secrets and tokens: `bedrock-agentcore:GetResourceApiKey`, `GetResourceOauth2Token`, `GetResourcePaymentToken`, `GetWorkloadAccessToken*`, `GetTokenVault`.
- Memory contents: `ListMemoryRecords`, `GetMemoryRecord`, `RetrieveMemoryRecords`, `ListEvents`, `GetEvent`, `ListSessions`, `ListActors`.
- Invocations: `GetAgentCard` (a data-plane call to the agent), `InvokeRegistryMcp`, `bedrock:InvokeAgent`, `bedrock:InvokeInlineAgent`, `bedrock:ApplyGuardrail`.
- The organization pieces stay only on the hub role.
- **AWS has no AgentCore read-only managed policy** (only `BedrockAgentCoreFullAccess`), so ACT must ship this custom policy.
- The `TracesReadOptIn` statement exists only when `param_IncludeTraces=true`.

**CloudFormation template outline** (`act-aws-readonly-v1.yaml`):

```yaml
Parameters:
  OidcIssuerUrl:   {Type: String}          # https://<host>/auth/realms/DominoRealm   (pre-filled)
  OidcAudience:    {Type: String}          # aud/azp of the Domino run token          (pre-filled)
  OidcSubject:     {Type: String}          # ACT service identity sub                 (pre-filled)
  CreateOidcProvider: {Type: String, AllowedValues: ["true","false"], Default: "true"}
  ExternalTrustMode: {Type: String, AllowedValues: [oidc, externalid], Default: oidc}
  ActPrincipalArn: {Type: String, Default: ""}   # externalid mode only
  ExternalId:      {Type: String, Default: ""}   # externalid mode only (ACT-generated, not secret)
  IncludeTraces:   {Type: String, AllowedValues: ["true","false"], Default: "false"}
  DeployToOrganization: {Type: String, AllowedValues: ["true","false"], Default: "false"}
  ReturnUrl:       {Type: String}          # ACT URL + #/connect/aws/callback?state=<nonce>
Conditions: {MakeOidc, UseOidc, UseExtId, Traces, Org}
Resources:
  OidcProvider: {Type: AWS::IAM::OIDCProvider, Condition: MakeOidc, Properties: {Url: !Ref OidcIssuerUrl, ClientIdList: [!Ref OidcAudience]}}
  ActReadOnlyRole:
    Type: AWS::IAM::Role   # RoleName: ACT-ReadOnly ; MaxSessionDuration: 3600 ; Tags: act:template-version=1
    # Trust (oidc): Federated arn:aws:iam::${AWS::AccountId}:oidc-provider/<host>/auth/realms/DominoRealm,
    #   Action sts:AssumeRoleWithWebIdentity, Condition StringEquals {"<host>/auth/realms/DominoRealm:aud": OidcAudience,
    #   "<host>/auth/realms/DominoRealm:sub": OidcSubject}
    # Trust (externalid): AWS ActPrincipalArn, sts:AssumeRole, Condition StringEquals sts:ExternalId
    # Policies: the JSON above (traces statement conditional)
  MemberRoles:              # Condition: Org; spoke role "ACT-ReadOnly-Member" trusting the hub role ARN
    Type: AWS::CloudFormation::StackSet
    Properties: {PermissionModel: SERVICE_MANAGED, AutoDeployment: {Enabled: true, RetainStacksOnAccountRemoval: false},
                 Capabilities: [CAPABILITY_NAMED_IAM], StackInstancesGroup: [{DeploymentTargets: {OrganizationalUnitIds: [<root>]}, Regions: [us-east-1]}],
                 TemplateURL: <s3 member template>}
Outputs:
  RoleArn:     {Value: !GetAtt ActReadOnlyRole.Arn}
  ReturnToACT: {Value: !Sub "${ReturnUrl}&role=${ActReadOnlyRole.Arn}"}
```

Quick-create URL format:
`https://{r}.console.aws.amazon.com/cloudformation/home?region={r}#/stacks/create/review?templateURL=https://{bucket}.s3.{r}.amazonaws.com/act/act-aws-readonly-v1.yaml&stackName=ACT-ReadOnly&param_OidcIssuerUrl=…&param_OidcAudience=…&param_OidcSubject=…&param_ReturnUrl=<url-encoded>&param_DeployToOrganization=false`

All values are URL-encoded. `NoEcho` parameters are ignored, so put no secrets in the link. The IAM capability checkbox cannot be pre-ticked.

### Option B (fallback): same template, ExternalId trust
- **Use when:** the bank's security team refuses to trust a Keycloak issuer, or the deployment's issuer is not publicly reachable (private Domino).
- **Requires:** ACT needs its own AWS principal, for example the Domino pod's IRSA or EKS Pod Identity role (Domino Professional Services blueprints), or, as a last resort, an access key stored as a Domino secret.
- **User flow:** the same clicks with `param_ExternalTrustMode=externalid&param_ActPrincipalArn=…&param_ExternalId=<uuid>`.
- **What ACT stores:** the external ID (not secret) and, only in the access-key case, a secret.
- **Lifetime:** `AssumeRole` sessions last up to 1 hour.

### Option C: IAM Identity Center device code
- **Flow:** `sso-oidc RegisterClient` (public client) → `StartDeviceAuthorization` → the user approves in the browser → `CreateToken`.
- **Auto-discovery:** `sso:ListAccounts`, `ListAccountRoles` and `GetRoleCredentials` discover every account and role the user can reach, which is good scope discovery.
- **Cons:**
  - The user must **type the start URL** (`https://d-xxxx.awsapps.com/start`) and the Region.
  - Permissions are the person's own permission set, not a read-only role.
  - Sessions end at the session duration the bank's Identity Center admin configures, which breaks unattended sync. Refresh tokens carry their own expiry (`clientSecretExpiresAt`).
  - An authorization-code redirect to the App URL needs an admin-created customer-managed Identity Center application (`entitledApplicationArn`) that allows that redirect URI. Public clients are otherwise effectively loopback-only.
- **Verdict:** use it only to *bootstrap*. Sign in, then let ACT call `cloudformation:CreateStack` with Option A's template in the account(s) the user picks. That avoids the console round trip for users who have Identity Center admin rights.

### Option D: `aws login` (console credentials)
- AWS-owned public clients (`oauth2/public-client/localhost|remote`) for the AWS CLI and SDKs, with 15-minute credentials refreshed for up to 12 hours.
- Not offered to third-party web apps; the remote mode means pasting a code.
- **Not recommended.**

### Option E: access keys pasted in the wizard
A long-lived secret with high friction. Keep it hidden behind "Advanced", for air-gapped proofs of concept only.

**Recommendation:** **primary is Option A** (quick-create or organization StackSet, OIDC trust); **fallback is Option B** (the same template, ExternalId).

---

## 5. Auto-discovery of scopes after auth (no typing)
1. `sts:GetCallerIdentity` gives the home account. `organizations:DescribeOrganization` then shows whether this is the management or delegated-admin account; if so, `ListAccounts` (`Status=ACTIVE`) gives the spoke list. Each spoke role ARN is the deterministic `arn:aws:iam::{acct}:role/ACT-ReadOnly-Member`.
2. Regions per account: `account:ListRegions(RegionOptStatusContains=[ENABLED, ENABLED_BY_DEFAULT])` ∩ `boto3.Session().get_available_regions('bedrock-agentcore-control')` (22 Regions today, including GovCloud West) ∪ the `bedrock-agent` Regions. Agent Registry exists in only 9 commercial Regions plus GovCloud West, so skip it elsewhere.
3. Optional fast path, per Region: `agent-registry-control:ListRegistries`. If a registry has `autoDetection.status=ACTIVE`, take the per-account and per-Region counts from its records' `provenance.sourceId` without touching each account. This preview count is shown while the full crawl runs.
4. Per (account, Region), in parallel and rate-aware: `ListAgentRuntimes` + `ListHarnesses` + `bedrock-agent:ListAgents` give a count only.
5. Present the scope tree: account → Region → count. **Default to everything with more than 0 agents selected.** Empty Regions are collapsed. If an account has no spoke role yet (StackSet still rolling out), show "Waiting for role…" and retry automatically.

---

## 6. Wizard steps

| Step | User sees | Happens automatically |
|---|---|---|
| 1 Choose | AWS card: "Bedrock AgentCore + Bedrock Agents". Toggle: **This account** / **My whole AWS Organization**. Checkbox "Include traces (CloudWatch)", off by default. | ACT reads its token claims (`iss`, `aud`/`azp`, `sub`), builds the nonce and the quick-create URL |
| 2 Connect | "Open AWS console" button, a 3-bullet explainer of what the role can read (expandable policy JSON, plus a "Download template" and "Terraform" link for change review) | Waits for the return link; also offers "Paste role ARN instead" |
| 3 Verify | A checklist that ticks itself: Signed in to AWS ✓ · Read AgentCore ✓ · Read Bedrock Agents ✓ · Read IAM role policies ✓ · Organization ✓ · Traces – | `test_connection` probes (section 4); a missing item shows a fix plus a one-click "Update stack" link |
| 4 Scopes | Account and Region tree with counts; everything with agents is ticked | `list_scopes` (section 5) |
| 5 Crawl | "Scanning 123456789012 / us-east-1… 14 agents" progress, as a Domino Job | `discover` (section 7), per-scope errors don't stop the rest |
| 6 Review / Import | (from the import-wizard plan) new / changed / unchanged / missing, suggested tiers | — |

**Common failures**

| Error (code) | Where | Plain-language fix |
|---|---|---|
| CFN `InsufficientCapabilitiesException` / "Requires capabilities: [CAPABILITY_NAMED_IAM]" | Console | "Tick the IAM acknowledgment box at the bottom, then Create stack." |
| CFN "Provider with url … already exists" (`EntityAlreadyExists`) | Stack create | "Your account already trusts this Domino. Use this link instead," which re-opens with `CreateOidcProvider=false`. |
| CFN `AccessDenied` on `iam:CreateRole` | Stack create | "Your AWS user can't create roles. Send this link to your cloud admin" (offer a copy-link button). |
| STS `InvalidIdentityToken` ("Couldn't retrieve verification key…") | Verify | "AWS can't reach this Domino's sign-in keys. Ask your Domino admin whether `/auth/realms/DominoRealm` is public, or switch to External ID." |
| STS `InvalidIdentityToken` ("Incorrect token audience") | Verify | "The role expects a different Domino audience. Click Update stack to refresh it." |
| STS `AccessDenied` ("Not authorized to perform sts:AssumeRoleWithWebIdentity") | Verify | "The role trusts a different Domino identity (subject). Re-run from ACT, or check that the crawl runs as the ACT service account." |
| STS `ExpiredTokenException` / `IDPCommunicationError` | Any | Automatic retry with a fresh run token; if it persists: "Domino sign-in is temporarily unavailable." |
| `AccessDeniedException` (403) on a List call | Verify / crawl | "The ACT role is missing `<action>`. Click Update stack (template v{n})." |
| `ThrottlingException` (429) | Crawl | Automatic jittered backoff; shown only as "AWS asked us to slow down; this scan will take a bit longer." |
| `UnrecognizedClientException` / `InvalidClientTokenId` | Region | "This Region is disabled in your account" (it gets skipped). |
| `EndpointConnectionError` / `UnknownServiceError` | Region / SDK | "AgentCore (or Agent Registry) isn't offered in this Region" (skipped), or "ACT's AWS SDK is out of date". |
| `AWSOrganizationsNotInUseException` / `AccessDeniedForDependencyException` | Organization | "This account isn't the management or delegated-admin account. Connecting a single account instead." |
| `ResourceNotFoundException` (404) mid-crawl | Crawl | Agent deleted during the scan; ACT marks it "Missing" on the next crawl. |
| Spoke `AccessDenied` on `AssumeRole` | Organization crawl | "StackSet hasn't reached account X yet (or the account is suspended). Retrying in 5 minutes." |

---

## 7. Intake of one agent (full record)
**Runtime**
1. `GetAgentRuntime`, then `ListAgentRuntimeVersions` and `ListAgentRuntimeEndpoints`, then `ListTagsForResource`.
2. Role: `iam:GetRole` + attached and inline policies, then parse resources (data scopes), write actions (consequence) and `bedrock:InvokeModel` resources (models).
3. If `workloadIdentityDetails` is present: `GetWorkloadIdentity`. Then `ListOauth2CredentialProviders` / `ListApiKeyCredentialProviders` and match by name or env var (systems).
4. Env var **keys** only. Keep values only when they match `^arn:aws`, a model-ID pattern, or a `*.gateway.bedrock-agentcore.*` URL; **drop all other values** (they may be secrets).
5. Gateway match: env URL = `GetGateway.gatewayUrl`, then `ListGatewayTargets` → `GetGatewayTarget` gives the tool names, then `policyEngineConfiguration` (`ListPolicies` count).
6. Memory: env `MEMORY_ID` → `GetMemory` (name, strategies; never the records).
7. Registry: a record whose `provenance.sourceId` equals the ARN gives the display name, description, A2A card skills and custom metadata (owner, limits).
8. Owner fallback: `cloudtrail:LookupEvents(EventName=CreateAgentRuntime, ResourceName=<id>)`. It covers only 90 days and is limited to 2 TPS.
9. Optional traces: a Logs Insights count of sessions over 30 days, plus distinct `gen_ai.request.model` and tool span names. That confirms the models and tools actually used, as drift evidence.

**Harness:** `GetHarness` (model, tools, skills and role come directly), then steps 2, 6 and 7 above, then `ListHarnessVersions`.

**Classic agent**
1. `GetAgent`, then `ListAgentAliases` (to pick the live version).
2. `ListAgentActionGroups(version)` → `GetAgentActionGroup` each (function names, `requireConfirmation`, Lambda ARNs).
3. `ListAgentKnowledgeBases` and `ListAgentCollaborators`.
4. `GetGuardrail` (if one is configured) and `ListTagsForResource`, then the role (step 2 above).

---

## 8. Recorded-HTTP fixtures for contract tests
All fixtures live under `fixtures/http/aws/`. Record them with botocore event hooks (request plus response, account IDs scrubbed). Use the new `agent-registry` shapes; the existing `fixtures/aws/list-discoverable-registry-records.json` uses the retired preview shape (`descriptorType`, `descriptors.agent`) and must be replaced.

| # | Fixture | Purpose |
|---|---|---|
| 1 | `sts-assume-role-with-web-identity.ok.xml` / `.invalid-token.xml` / `.access-denied.xml` | Auth success, plus the two most common failures |
| 2 | `sts-get-caller-identity.ok.json` | Account ID |
| 3 | `account-list-regions.ok.json` (including an opted-out Region) | Region scoping |
| 4 | `organizations-describe-organization.ok.json`, `.not-in-use.json`, `organizations-list-accounts.page1.json` + `.page2.json` | Organization detection and pagination |
| 5 | `agentcore-list-agent-runtimes.page1.json` (with `nextToken`), `.page2.json`, `.empty.json` | Pagination and an empty Region |
| 6 | `agentcore-list-agent-runtimes.throttled.json` (429 `ThrottlingException`), then `.ok` | Backoff |
| 7 | `agentcore-list-agent-runtimes.access-denied.json` (403) | Missing permission, mapped to the fix text |
| 8 | `agentcore-get-agent-runtime.{ready,create-failed,vpc,jwt-authorizer}.json` | Field mapping and status variants (env values include a fake secret to prove redaction) |
| 9 | `agentcore-list-agent-runtime-versions.json`, `-endpoints.json`, `list-tags-for-resource.json` | Versions, endpoints and owner |
| 10 | `agentcore-list-harnesses.json`, `get-harness.bedrock-model.json`, `get-harness.openai-model.json` | Harness mapping |
| 11 | `agentcore-list-gateways.json`, `get-gateway.json`, `list-gateway-targets.json`, `get-gateway-target.lambda-tools.json`, `.mcp-server.json` | Tools and policy engine |
| 12 | `agentcore-list-workload-identities.json`, `list-oauth2-credential-providers.json`, `get-memory.json` | Identity, systems and memory |
| 13 | `iam-get-role.json`, `iam-list-attached-role-policies.json`, `iam-get-policy-version.json`, `iam-get-role-policy.json` | Data scopes and model IDs |
| 14 | `registry-list-registries.org-autodetect-active.json`, `registry-list-registry-records.page1/2.json`, `registry-get-registry-record.autodetected.json`, `registry-batch-get.partial-error.json` | Organization fast path, provenance and partial failure |
| 15 | `bedrock-agent-list-agents.page1/2.json`, `get-agent.json`, `list-agent-aliases.json`, `list-agent-action-groups.json`, `get-agent-action-group.function-schema.json`, `list-agent-knowledge-bases.json` | Classic agents |
| 16 | `bedrock-agent-list-agents.access-denied.json` | Classic agents not permitted, while the AgentCore part still succeeds |
| 17 | `endpoint-unavailable.region-ap-southeast-7.txt` (connection error) | Service not in that Region |
| 18 | `logs-start-query.json`, `logs-get-query-results.{running,complete}.json`, `cloudwatch-get-metric-data.json` | Runs and traces opt-in |
| 19 | `cloudtrail-lookup-events.create-agent-runtime.json` | Creator fallback |

---

## 9. Risks, unknowns, sources

**Risks and unknowns** (the top three come first)
1. **Today's adapter and fixtures model retired or invented shapes.**
   - The registry moved to `agent-registry` on 2026-08-06; the old API shut down 2026-10-30, and its fields were renamed: `descriptorType` → `recordType`, `name` → `displayName`, `version` → `recordVersion`, `inlineContent` → `data`.
   - The runtime `metadata` block in the fixture does not exist in the real API.
   - Tools, models and data scopes for Runtimes must be **inferred** from Gateways, IAM, env vars and spans, so some agents will have incomplete records. Show the confidence per field.
2. **Domino OIDC trust is new ground for a bank's security review.**
   - Any Domino run token with a matching `aud` and `sub` can assume the role, so the `sub` pin is essential.
   - It is still open whether the run token's `sub` is the App publisher, the viewer, or the user who starts a Job. Crawl Jobs launched "as the viewer" would break the pin.
   - Recommendation: a dedicated Domino service account as the ACT identity, with Jobs launched as it.
   - Also unverified: the token's `aud`/`azp`, its lifetime, and whether private deployments expose the JWKS. Decode the claims once in a run (do not log the token) before building.
3. **Quick-create needs a vendor-owned public S3 template**, and some banks block console quick-create or third-party templates, or demand Terraform. Ship the same role as a downloadable template plus a Terraform module, and version it with the `act:template-version` tag so ACT can detect drift. Two console behaviours also need hands-on checks:
   - Whether URL outputs render as clickable links.
   - Whether service-managed StackSets skip the management account (the hub role covers it).
4. `GetAgentRuntime` returns `environmentVariables` in clear text, so ACT will receive secrets agents mis-store there. Redact them at the adapter boundary and never persist values.
5. Several "Read"-level IAM actions return tokens or memory contents. The policy excludes them, but `bedrock-agentcore:*`-style wildcards from customers would not.
6. API churn: harness (GA June 2026), Runtime V2 and the registry schema all changed in 2026. Pin botocore, record fixtures, and add an `UnknownServiceError` guard.
7. Rate limits are low:
   - Runtime List calls share 25 TPS.
   - Memory `ListMemories` is 5 TPS.
   - Policy is 5 TPS.
   - CloudTrail `LookupEvents` is 2 TPS.
   - IAM is global.

   A 50-account by 10-Region organization needs per-(account, Region) token buckets, plus caching by role ARN.
8. Trace logs hold `request_payload` (PII), so they stay opt-in and limited to aggregates. Classic Bedrock Agents have no run API.
9. Organization auto-detection covers only Runtimes and Gateways (not harnesses or classic agents), and only in 9 Regions; the initial sync can take 20 minutes.
10. `ListHarnesses` summary fields and the exact wire names of auto-detected `http` and `agui` descriptors were not visible in the docs. Confirm them by recording.

**Sources** (all accessed 2026-11-12)
- AgentCore release notes: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html
- Control-plane operations: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_Operations.html
- GetAgentRuntime: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetAgentRuntime.html
- ListAgentRuntimes: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_ListAgentRuntimes.html
- GetHarness: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetHarness.html
- Harness type: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_Harness.html
- HarnessModelConfiguration: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_HarnessModelConfiguration.html
- HarnessBedrockModelConfig: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_HarnessBedrockModelConfig.html
- GetGatewayTarget: https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_GetGatewayTarget.html
- Quotas: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html
- Regions: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html
- AWS managed policies (no read-only one exists): https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/security-iam-awsmanpol.html
- Agent Registry overview: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html
- Registry with Organizations (auto-detection): https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-organizations.html
- Registry IAM permissions: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-iam-permissions.html
- Registry namespace migration: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-faq.html
- Registry RAM sharing: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-cross-account-sharing.html
- Registry MCP endpoint: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-mcp-endpoint.html
- Registry in four more Regions (Oct 2026): https://aws.amazon.com/about-aws/whats-new/2026/10/aws-agent-registry-four-regions/
- Observability overview: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html
- Service-provided observability data: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-service-provided.html
- Runtime metrics, spans and logs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-runtime-metrics.html
- Bedrock GetAgent: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_GetAgent.html
- CloudFormation quick-create links: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cfn-console-create-stacks-quick-create-links.html
- IAM OIDC provider: https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html
- Identity Center RegisterClient: https://docs.aws.amazon.com/singlesignon/latest/OIDCAPIReference/API_RegisterClient.html
- `aws login`: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html
- AWS MCP Server GA: https://aws.amazon.com/blogs/aws/the-aws-mcp-server-is-now-generally-available/
- Domino Apps enhanced identity (JWKS): https://docs.domino.ai/cloud/platform-capabilities/core-concepts/apps/identity
- Live check, se-demo OIDC discovery: https://se-demo.domino.tech/auth/realms/DominoRealm/.well-known/openid-configuration
- IAM action names were cross-checked against the community dataset https://github.com/iann0036/iam-dataset, because the Service Authorization Reference page did not render. Verify them with the IAM policy simulator before shipping.
