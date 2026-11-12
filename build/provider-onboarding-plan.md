# Plan: Connect a provider, then intake agents

Status: APPROVED 2026-11-12 (Nick: "get going"). Demo grade: sample tenants first, no security hardening. Decisions taken: D-1 act-svc service account created; D-2 no test accounts (sample + mocked HTTP); D-3 one Google Cloud card; D-4 sync default Daily 02:00; D-5 sample agents importable into Fleet with the "Sample data" badge; D-6 this plan runs before the remaining Superblocks tasks.
Supersedes the import-wizard plan (build/import-wizard-plan.md, T-40..T-46) and Superblocks T-27/T-31. Its adapter contract and "crawl as a Domino Job" survive.
Inputs:
- research: build/research/providers/{aws-agentcore, google-vertex, google-agents, azure-foundry, databricks, snowflake, domino-token}.md
- design spec: build/design/provider-onboarding.md
- clickable prototype: build/design/provider-onboarding.html, served at <workspace>/proxy/9201/provider-onboarding.html

## 1. What Nick asked for
- A gallery of provider cards.
- Each card opens an onboarding wizard with modern auth: no typed URLs, no pasted secrets.
- Discovery with minimal interaction, then frictionless intake of agents from that provider.
- Providers: AWS AgentCore, Google (Vertex / Agent Runtime and Gemini Enterprise), Azure AI Foundry, Databricks, and Snowflake Cortex Agents. Domino stays native and the A2A URL becomes "Other".

## 2. What the research settled
| Provider | Connect (primary) | User effort | Fallback | Inventory |
|---|---|---|---|---|
| AWS | CloudFormation quick-create link: a read-only role trusting Domino's OIDC issuer (StackSets for an Org) | ~6 clicks, 0 typed | same template with external ID; paste the role ARN if the return link doesn't render | bedrock-agentcore-control, agent-registry-control (new namespace, 2026-08), bedrock-agent |
| Google Cloud (one card, both products) | Workload Identity Federation via an ACT-generated Cloud Shell command; prints "Finish in ACT" | ~7 clicks, 1 paste, 0 typed | "Sign in with Google" (customer OAuth client) | Agent Runtime reasoningEngines (Cloud Asset search for org-wide), Agent Registry v1, Gemini Enterprise v1alpha agents |
| Microsoft | User-assigned managed identity with a federated credential; one Cloud Shell command; "Return to ACT" | pick subscription + 1 paste | "Sign in with Microsoft" (multi-tenant app) | Resource Graph (AIServices) → project endpoints built by ACT; Foundry /agents (v1); Graph agent identities; Agent 365 catalog (optional) |
| Databricks | Federated service principal (token federation policy); ACT-generated CLI/Terraform script prints a connection code | 1 paste (0 on Azure via Microsoft sign-in) | "Sign in with Databricks" (PKCE) | serving endpoints (agent task types), Apps, Agent Bricks (Beta), UC models, functions and grants, MLflow traces |
| Snowflake | Workload Identity Federation service user; ACT-generated SQL worksheet whose last row is "Finish in ACT" | 3-4 clicks + paste, Run All, 0 typed | Snowflake OAuth (PKCE, 90-day refresh), then key pair | SQL API: SHOW AGENTS IN ACCOUNT, DESCRIBE AGENT, grants, observability |
| Domino | nothing; uses the app's identity | 0 | — | existing live adapter |
| Other | A2A card URL (today's Register flow) | 1 URL | — | — |

Cross-cutting findings that shape the build:
1. **Secretless rests on Domino's token** (domino-token.md):
   - iss is public and the JWKS is reachable.
   - The lifetime is 300 s, so ACT fetches a fresh token for each exchange.
   - aud is a shared list of 6 values, and sub is the run's user.
   - So the trust must pin sub to one **dedicated Domino service account** that runs the App and the scan Jobs. A Keycloak audience mapper ("act-federation") makes the pin strong. Without it, ACT pins iss + sub + aud=domino-platform, logged as a known limitation.
2. **The existing cloud fixtures and adapters use invented shapes** (AWS, Google, Microsoft and Databricks all confirmed). The adapters are rebuilt on the real APIs with recorded-HTTP contract tests. The demo fixtures are re-shaped to real responses while keeping the demo story and the Fleet counts.
3. **Read-only must be built, not assumed:**
   - Several vendor "viewer/user" roles can invoke agents or write.
   - Each provider ships a custom read-only role or policy, and ACT checks it doesn't hold invoke or write rights.
   - Env vars, connection keys and similar values come back in clear text and are redacted before storage.
4. **Visibility gaps are silent:** Databricks per-object ACLs, Snowflake SHOW AGENTS filtering by grants, and the Gemini Enterprise "created by caller" limit. Each adapter reports a coverage check ("ACT may not see N agents: <fix>") instead of a false "all clear".

## 3. Tasks
Builder for every task is `builder` unless noted. QA: test-author + code-reviewer on backend tasks, plus ui-tester + visual-reviewer on UI tasks. Design source for UI tasks: build/design/provider-onboarding.md and .html.

| Id | Title | Depends | Main files |
|---|---|---|---|
| PO-1 | Connections core: tables, handshake state machine, callback, paste-anything parser, setup-artifact generator, federation token exchange, redaction, sample-tenant mode | T-20 merged first only if it touches main.py (else none) | backend/connections/ (new package), backend/routes/connections.py, backend/db.py (additive), templates/provisioning/ |
| PO-2 | Suggested criteria engine with a "why" per score; override needs a reason, audited (was T-42) | — | config/criteria_rules.yaml, backend/suggest.py, backend/routes/suggest.py |
| PO-3a | AWS + Snowflake adapters on real APIs: auth_methods, test_connection (permission checklist with fixes), list_scopes, discover, coverage check, recorded HTTP | PO-1 | adapters/aws.py, adapters/snowflake.py, fixtures/http/{aws,snowflake}/, fixtures/aws/ |
| PO-3b | Google (Agent Runtime + Registry + Gemini Enterprise, merged by identity) + Microsoft (Foundry + Entra Agent ID + Agent 365) adapters, same contract | PO-1 | adapters/google.py, adapters/microsoft.py, fixtures/http/{google,microsoft}/, fixtures/google/, fixtures/microsoft/ |
| PO-3c | Databricks adapter (endpoints, Apps, Agent Bricks, UC) + Domino adapter on the same contract | PO-1 | adapters/databricks.py, adapters/domino.py, fixtures/http/databricks/, fixtures/databricks/ |
| PO-4 | Scan + intake backend: jobs/discover.py launched as a Domino Job, progress, staging diff, import (Pending review, bundle, audit, re-tier), Awaiting-import queue, scheduled sync, changed/missing findings | PO-1, PO-2 | jobs/discover.py, backend/discovery.py, backend/routes/discovery.py |
| PO-5 | UI part 1: Connections nav, gallery, wizard steps 1-3 (HandshakePanel with live detection, PermissionChecklist, ScopeTree), callback landing, /register redirect, connect.css tokens | PO-1, T-20 | frontend/src/pages/Connections.jsx, Connect*.jsx, components/connect/, connect.css, ui.jsx (nav), main.jsx (routes), dist |
| PO-6 | UI part 2: ScanView, IntakeReviewTable (diff, suggested tier with why, overrides, bulk), ImportSummary, connection detail, single-agent intake, Fleet "Add agents" + awaiting-import banner | PO-4, PO-5 | frontend/src/pages/Scan*.jsx, Review*.jsx, ConnectionDetail.jsx, Intake.jsx, Fleet.jsx, dist |
| PO-7 | Demo + publish: sample tenants for all five clouds, seeded scan with new/changed agents, demo walk captures; publish the App (SR-6) | PO-6 | jobs/seed_demo.py, docs/DEMO.md; domino-deploy (sonnet) |
| PO-L | Live proof, once Nick provides a sandbox: one provider end to end from a Job and the published App as the service account | PO-3x for that provider, decision D-1 | none (records results); domino-setup (sonnet) |

Waves (at most 3 tasks; Small tier: one browser suite at a time):
- **PA:** T-20 (nav and routes; it no longer builds Connections/Import pages, it only reserves the nav slot), PO-1, PO-2
- **PB:** PO-3a, PO-3b, PO-3c
- **PC:** PO-4, PO-5, T-19f
- **PD:** PO-6, T-48f/T-50f (small)
- **PE:** PO-7, then PO-L when a sandbox exists

The remaining Superblocks tasks (T-21..T-26, T-28..T-30, T-32/T-33) follow after PE in the existing order. T-27/T-31/T-40..T-46 are retired.

## 4. Acceptance criteria

**PO-1 Connections core (demo grade)**
- PO-1.1 POST /api/connections {provider, sample?} creates a connection in state draft with a one-time state token, and returns the provider's setup artifact pre-filled with the Domino issuer (https://se-demo.domino.tech/auth/realms/DominoRealm), the act-svc subject, audience "domino-platform", the state token and the App's return URL:
  - aws: a CloudFormation quick-create URL plus the template body (a read-only role trusting the issuer)
  - google: a Cloud Shell command
  - microsoft: a Cloud Shell (az) command
  - databricks: a CLI script that prints a connection code
  - snowflake: a SQL worksheet whose last SELECT builds the "Finish in ACT" link
  Artifacts are text generated from templates in templates/provisioning/. They only need to look right and be copy-pasteable, not be validated against a live cloud.
- PO-1.2 States: draft → waiting → trusted → verified | partial → connected, plus needs_attention | failed. GET /api/connections lists them and GET /api/connections/{id} returns one, with state, provider, sample flag, scopes, last check, last scan and agent count. DELETE disconnects: the connection is marked disconnected and its agents are kept.
- PO-1.3 Callback GET /api/connections/callback?state=… moves the matching connection to trusted. An unknown or already-used state returns a plain-language error, not a status code.
- PO-1.4 Paste-anything: POST /api/connections/{id}/paste {text} classifies the input. It recognises:
  - an AWS role ARN
  - a GCP project id or number, or a WIF provider path
  - an Azure subscription, tenant or client id (GUIDs)
  - a Databricks account id, connection code or Databricks URL
  - a Snowflake Snowsight URL or account identifier
  - an A2A agent-card URL, answered with "use Other"
  - anything else, answered with "not recognised" plus examples
  A recognised value moves the connection to trusted. Unit-tested with realistic samples.
- PO-1.5 Sample mode (the demo path). A connection created with sample=true:
  - walks the whole flow with no cloud: draft → waiting, then trusted automatically after about 3 s, detected by GET polling (no user action)
  - check-access returns a realistic permission checklist with one "missing" item and its plain-language fix, which "Re-check" then turns green
  - scopes come from the sample tenant
  - it is flagged sample=true in every response
  Sample tenants are seeded for all five clouds from fixtures.
- PO-1.6 POST /api/connections/{id}/check returns the per-permission ok/missing list (from the adapter's test_connection, or the sample data) and moves the state to verified or partial. GET /api/connections/{id}/scopes returns the scope tree, all selected by default. PUT /api/connections/{id}/scopes saves the selection.
- PO-1.7 Federation helper: backend/connections/federation.py has one function per provider that turns a fresh Domino token (localhost:8899/access-token) into a provider credential (AWS STS AssumeRoleWithWebIdentity, Google STS + SA impersonation, Entra client assertion, Databricks /oidc/v1/token, Snowflake WIF bearer). Each is unit-tested with mocked HTTP. Live use is out of scope until there is a test account.
- PO-1.8 Connect, disconnect and paste write audit events with the Domino user, reusing the existing audit/event helpers.
Dropped for the demo (Nick 2026-11-12): encryption extras, secret-scan tests, per-provider redaction tests, user-bound state tokens, trust-pinning checks.

**PO-2 Suggested scores** (was IW-5)
- All seven scores are pre-filled, each with a one-line reason citing the tool, scope or setting that drove it.
- The same input gives the same output.
- An override requires a reason and writes an audit event.
- Rules live in config/criteria_rules.yaml.

**PO-3a/b/c Adapters** (per provider; the same ACs for each)
- PO-3.1 auth_methods() lists the primary and fallback methods. test_connection() returns a per-permission ok/missing list with the exact plain-language fix from the research failure table. It also flags any granted permission that allows invoke or write ("ACT asked for read-only; this role can also <x>").
- PO-3.2 list_scopes() returns accounts/regions, projects/locations, subscriptions/projects, workspaces, or databases/schemas, all selected by default, with no typed input.
- PO-3.3 discover() returns SourceAgents mapped per the research mapping table. Tools, model, identity, data scopes, versions, owner hints and status are filled where the API provides them; values the API can't provide are marked "unknown", never invented. Raw payloads pass redact().
- PO-3.4 Contract tests run against recorded HTTP for success, missing permission, throttling (retry with backoff honouring Retry-After) and pagination.
- PO-3.5 Coverage check: the adapter reports agents it may be missing and why (per research §9), with a fix.
- PO-3.6 Demo fixtures are re-shaped to real API responses, intake still yields the same Fleet (13 agents, the same names and the treasury-sweep scenario), and existing AC-1..AC-3 tests still pass.
- PO-3b extra: one Google connection covers Agent Runtime, Agent Registry and Gemini Enterprise, and one Microsoft connection covers Foundry, Entra Agent ID and Agent 365 (optional, licence-gated). Duplicates merge on the source identity.

**PO-4 Scan and intake** (was IW-3/4/6/8)
- PO-4.1 Starting a scan launches a Domino Job (its id and link are returned). Progress updates at least every 5 s with per-scope counts and errors. One failing scope doesn't stop the others. Closing the browser doesn't stop the scan.
- PO-4.2 Results are classed New/Changed/Unchanged/Missing by (platform, native id), with a field-level diff for Changed. A second scan with no source change produces no New or Changed rows.
- PO-4.3 Import:
  - Selected rows become agents in Pending review, with tier (from PO-2 plus overrides), owner, manifest, identity, dependencies, a Governance bundle and an audit event naming the user, connection and scan.
  - Changed agents re-tier and return to review if their tier rose.
  - Summary counts match the selection.
- PO-4.4 Sync:
  - A schedule (off/daily; default per D-4) runs as a scheduled Domino Job.
  - New agents land in Awaiting import and are never auto-imported.
  - Changes and disappearances create findings.
  - Sample agents are importable only if D-5 says so, and are excluded from evidence packs.

**PO-5 UI part 1**
- All per design spec §2 routes, §3.1-3.4, §4.1-4.6, §5 copy, §8 testids and §9 checks.
- PO-5.1 Nav reads Fleet · Connections · Settings. /register redirects to /connections/add-by-url, and the Register flow and its tests still pass there.
- PO-5.2 The gallery shows six provider cards plus Other, each with not-connected / connected (count, last sync) / needs-attention / sample states.
- PO-5.3 HandshakePanel:
  - One-click open button, copyable artifact, and "ACT will read / can't / stores / who can do this" before trust.
  - Live detection with aria-live, which advances on its own when trust is detected, with no "I'm done" button.
  - A paste-anything fallback.
  - Secret methods sit under "Other ways to connect", labelled "Not recommended" with the reason.
- PO-5.4 The permission checklist shows a fix per missing item and a re-check button. The scope tree is all selected by default, with select-all, keyboard support and counts.
- PO-5.5 Every step has designed loading, empty and error states. There's no horizontal scroll at 1024 or 1440, keyboard focus moves to the step heading on step change, and reduced motion is respected.

**PO-6 UI part 2**
- Per design spec §3.5-3.9 and §4.7-4.10.
- PO-6.1 ScanView streams counters and agent rows live (aria-live polite, throttled), shows the Job link, and survives a reload.
- PO-6.2 Review table:
  - New/Changed/Unchanged/Missing tabs with counts; Changed rows show a diff.
  - Suggested tier with "why" per criterion; an override needs a reason.
  - Risk flags: Tier 1, no distinct identity, write scopes, no owner, redacted secrets found.
  - Bulk select and bulk owner; the import button states the count.
- PO-6.3 The import summary shows counts with links. Connection detail shows status, the awaiting-import queue, access, scope, history, and Test / Scan now / Reconnect / Disconnect (disconnect keeps records and marks them "source disconnected").
- PO-6.4 Fleet:
  - The primary action "Add agents" goes to /connections; "Add by URL" keeps data-testid register-external-button.
  - An awaiting-import banner appears when the queue is non-empty.
  - Agent pages link their source chip to the connection.
- PO-6.5 Sample data shows the striped "Sample data" badge everywhere it appears and never looks live.

**PO-7 Demo and publish**
- A cold start shows connected sample tenants for all five clouds and a fresh scan with new and changed agents.
- The demo walk goes connect → scan → review → import in under 3 minutes, with captures.
- The App is published as a Domino App, and its id and URL are recorded in STATUS.md.

## 5. Estimate
10 tasks. At this session's per-task cost ($27-44), that's about $350-450 including QA rounds. On Small, at most 2-3 tasks run at once and a full e2e run takes ~2 min.

## 6. Decisions for Nick
- **D-1 Service account.** Create a dedicated Domino service account that publishes the App and runs scan Jobs, so every cloud trusts exactly one identity. Optionally, a Domino admin also adds a Keycloak audience "act-federation" for a strong pin. Recommended: yes. Without it, the trust breaks whenever someone else publishes or runs a scan.
- **D-2 Sandbox accounts.** Any of AWS, GCP, Azure, Databricks or Snowflake we can use for a live proof (PO-L)? Without them, the clouds run on sample tenants and the live paths are proven by recorded-HTTP contract tests only.
- **D-3 Google.** One "Google Cloud" card naming Vertex/Agent Runtime and Gemini Enterprise (recommended; one sign-in), or two cards sharing one connection.
- **D-4 Sync default** after import: Daily 02:00 (design default) or Off.
- **D-5 Sample agents:** importable into Fleet (labelled, excluded from evidence packs), or review-only. Recommended: importable, since the demo Fleet already uses sample data.
- **D-6 Order.** This plan runs right after T-20, ahead of the remaining Superblocks tasks. Recommended: yes.
