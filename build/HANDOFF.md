# ACT build spec and handoff (Agent Control Tower)

Spec-driven handoff for Claude Code in a Domino workspace, built with the domino-ai-plugin. Build from this document. Where a line says TODO:, stop and confirm with the requester before building that part.

Requester: Nick Goble (Solution Architecture, FSI). Prepared 2026-11-09. Target: a demo-able POC by 2026-11-13. Budget: $1,900.

> Setup notes (2026-11-09): Domino project is `ai-control-tower` (Git-based); project dataset is `/mnt/data/ai-control-tower`; integration branch is `main`. Wherever this document's original text assumed the project name `act`, the resolved paths are: QA dataset `/mnt/data/ai-control-tower/qa`, SQLite DB `/mnt/data/ai-control-tower/act/act.db`.

## 0. Before you build (read first)

This build runs as a team of Claude Code agents. The Domino plugin (dominodatalab@domino-marketplace, from github.com/dominodatalab/domino-ai-plugin) provides the platform skills and the domino-setup, domino-deploy, and domino-debug agents. Appendix A adds an orchestrator, a general builder, and the QA agents that test the work. Setup takes one session:

1. Save this document to /mnt/code/build/HANDOFF.md.
2. Write every file in Appendix A to the path its heading names, fill its TODO values from Section 8, add .claude/cost-ledger.json and .worktrees/ to .gitignore, and commit and push on the integration branch.
3. Run `bash build/qa/preflight.sh`. It installs the Domino plugin if it is missing, updates it when a newer release exists, and checks Playwright, the QA dataset folder, the budget, and push access to the git remote. On a FAIL line, stop and show the user the output.
4. Ask the user to run /exit and start claude again. The project settings start the new session as domino-orchestrator, which writes build/plan.md and runs the build from Section 9.

These rules hold for the whole build:

- **Section 7 wins over skill examples.** Load the plugin skill each component names, and where its example code shows a hardcoded host, X-Domino-Api-Key, or a legacy SDK call, follow Section 7 instead.
- **App UI follows the design source in Section 4.6:** the domino-app-guidelines skill (standard Domino look, loaded separately), with the plugin's domino-ui-bootstrap as the fallback. visual-reviewer checks screenshots against the same source.
- **The app persists state.** Follow the pattern in Section 4.7 (one SQLite DB on the project dataset, pooled and pre-warmed, caller identity from the proxy headers) rather than inventing a storage layer.
- **Scope discipline.** ACT is an evidence layer. It registers, tiers, traces, reconstructs, detects drift, and produces evidence. It does not enforce policy in any runtime, does not host a gateway, and does not issue identities. If a task drifts toward enforcement, stop and report.

## 1. End goal

ACT is the independent agent system of record for a bank: it registers every AI agent wherever it runs, tiers it by consequence under OCC Bulletin 2026-54, binds it to an owner and an identity, ingests its traces, reconstructs what it did, detects scope drift against its declared permitted actions, and produces an examiner-ordered evidence pack. This POC proves the thesis on a mixed estate: four simulated hyperscaler feeds (AWS AgentCore, Google Agent Platform, Microsoft Agent 365, Databricks Unity Catalog) replayed from fixtures that match the real API shapes, plus one real agent running in Domino. The headline demo is the "Hugging Face at a bank" scenario: a registered agent starts calling tools outside its manifest on a Friday night, and on Monday a risk officer reconstructs every step in under a minute and hands MRM a dated evidence pack. In July 2026, Hugging Face needed a weekend and a self-hosted model to do that.

- **Audience / persona:** a bank's second line (GenAI Model Risk Manager, Non-Financial Risk) and the Firmwide AI Platform owner who integrates it. Morgan Stanley is the first target.
- **The outcome that has to land:** the risk officer sees one inventory across clouds, opens one agent, and gets the 2026-54 evidence pack and a full run reconstruction without anyone filling in a form.
- **Definition of done:** AC-1 through AC-11 pass (Section 3); the demo script in Section 6 runs end to end from a cold start of the app in under ten minutes; the evidence pack for the real Domino agent is accepted by Nick as something he would show an MRM reviewer.

## 2. Business & regulatory context

Vertical: financial services (G-SIB, US-regulated, with EU and APAC operations).

Regulatory / compliance constraints:

- **OCC Bulletin 2026-54** (Nov 4, 2026), the examiner framework for GenAI and agentic systems: seven criteria, four consequence-based tiers, human oversight that is "more than a nominal click", records sufficient to reconstruct a material decision (its Section 2.14), permitted actions stated in testable terms (its Section 2.1).
- **SR 26-2 / OCC 2026-13** (Apr 17, 2026): traditional models stay in the MRM inventory; GenAI and agentic AI are out of its scope. ACT holds one record carrying both a model policy and an agentic policy.
- **FSB Sound Practices** (Oct 15, 2026): unique agent identifier; trace of triggers, tools, data, permissions; the agent's own reasoning summary is not a record.
- **HKMA Discussion Paper** (Nov 2, 2026): per-deployment record; five agentic incident types (acting beyond authority, stop-mechanism failure, credential compromise, silent monitoring failure, improper approval pass-through).
- **FINRA 15-09 and MiFID II RTS 6** for any trading-adjacent agent: retrievable versions, test records, change management.
- **Books and records:** treat agent traces as records by default; the POC stores them append-only with a hash chain. WORM storage is out of POC scope.

Governance evidence to collect: one Domino Governance bundle per registered agent carrying the tier decision, the permitted-action manifest, identity binding, the validation record (for the Domino-hosted agent), drift findings, approvals with builder/approver separation, and the generated evidence pack. Experiment lineage and traces for the Domino-hosted agent via MLflow. ACT's own audit events (who registered, re-tiered, approved) stamped with the Domino user id.

## 3. Acceptance criteria

Written Given/When/Then, verifiable. "Fixture" means the replay files under fixtures/ that mirror each platform's documented API response shape.

- **AC-1 Intake across runtimes.** Given the four fixture feeds and a live Domino project containing at least one deployed Agent, when the intake job runs, then the inventory lists every agent from all five sources with a stable ACT id, source platform, source-native id, owner, and last_seen, and a second run creates no duplicates.
- **AC-2 Normalized Agent Record.** Given any registered agent, when its record is fetched via GET /api/agents/{id}, then it validates against schemas/agent-record.json (A2A Agent Card fields, OASF skill and domain tags, and the bank fields: owner, tier, criteria scores, permitted-action manifest, identity binding, model dependencies, status, versions).
- **AC-3 Tiering by consequence.** Given an agent with the seven 2026-54 criteria scored, when the tier scorer runs, then it returns Tier 1 to 4 with the weights applied as configured in config/tiering.yaml, stores the reasoning string, and a change to the agent's tools or permissions in a later intake run re-tiers the agent and writes a retier audit event. Backtest or benchmark inputs are rejected as tier inputs (HTTP 422 with a message naming the rule).
- **AC-4 Trace ingest.** Given an OTLP/HTTP POST to /v1/traces carrying spans in OTel GenAI conventions (invoke_agent, execute_tool, gen_ai.agent.id), OpenInference attributes, or MLflow-exported spans, when the request is accepted, then each span is stored raw and as a canonical event (schemas/canonical-event.json) linked to the ACT agent id, and the events table's hash chain verifies end to end (GET /api/audit/verify returns {"ok": true, "events": N}).
- **AC-5 Reconstruction.** Given a run id for any ingested agent, when GET /api/runs/{run_id}/timeline is called, then it returns the ordered sequence of inputs, model calls, tool calls with arguments (redacted per config/redaction.yaml), outputs, approvals, and overrides, and the UI renders the same timeline, within 2 seconds for a run of up to 500 spans.
- **AC-6 Scope-drift detection.** Given an agent whose manifest allows tools {A, B} and a run that calls tool C, when the run is ingested, then a finding of type scope_drift with severity derived from the agent's tier is created within 5 seconds, names the tool, the span, and the timestamp, and appears on the agent's page. A run that calls only {A, B} creates no finding.
- **AC-7 Evidence pack.** Given a registered agent, when the user clicks "Generate evidence pack", then a Markdown and a PDF file are produced in the 2026-54 order (identity and ownership, tier and criteria, permitted actions, validation, monitoring and findings, approvals and overrides, records and retention), every section cites the record it came from, and the files are attached as evidence to the agent's Domino Governance bundle (or, if T-8b's fallback is active, saved under the project dataset with the bundle id noted).
- **AC-8 One inventory, two control sets.** Given the Domino-hosted agent that calls a registered model, when its record is viewed, then the page shows the agentic policy (ACT 2026-54 policy) and the model policy (SR 26-2 template) on the same bundle, and the dependency graph lists the model with its registry version.
- **AC-9 Approvals with separation.** Given an agent record awaiting approval, when the user who registered it attempts to approve, then the approval is refused with HTTP 403 and an audit event; when a different Domino user approves, then the approval is recorded with an expiry date set from the tier (Tier 1: 90 days, Tier 2: 180, Tier 3: 365, Tier 4: 365) and the record status becomes approved.
- **AC-10 Validation record (Domino agent).** Given the Domino-hosted agent and an evaluation set of at least 20 prompts, when the validation job runs with 5 repetitions per prompt, then MLflow holds one run per repetition, ACT stores the score distribution (mean, p5, p95, pass rate) and the judge model alias and version, and the validation record appears in the evidence pack.
- **AC-11 Client-agent intake.** Given an A2A Agent Card served at a URL (the fixture server's /.well-known/agent-card.json), when the user registers it as an external agent, then ACT fetches the card, verifies its JWS signature when present (and records unsigned when absent), creates a record with status pending_review and tier computed from the card's declared skills and the user's criteria answers, and the record carries a passport view (identity, allowed skills, data scopes, expiry).
- **AC-12 Demo scenario.** Given the "weekend drift" fixture run, when it is ingested, then the fleet page shows the agent with a Tier 1 badge and one open scope_drift finding, the finding links to the reconstructed timeline, and the evidence pack generated afterward includes the finding in its monitoring section.

**Verification (built it right):** AC-1 to AC-4, AC-6 backend, AC-8 backend, AC-9, AC-10, AC-11 backend by pytest (test-author); AC-5, AC-6 UI, AC-7, AC-8 UI, AC-11 UI, AC-12 by Playwright through the proxy URL (ui-tester) with visual-reviewer on screenshots. **Validation (built the right thing):** Nick walks the Section 6 demo script as the risk officer and signs off on the evidence pack content; one Domino colleague who has done MRM work reviews the pack against the 2026-54 order.

## 4. Project components

Only the components in scope. Each states what, not how, and names the skill, the building agent, and the QA agent that verifies it.

### 4.1 Model / agentic experiments

- **What:** one real agent hosted in Domino (agents/recon_agent): a small reconciliation assistant modeled on the public FIXR description, which reads two fixture ledgers, calls three tools (fetch_ledger, compare_entries, propose_adjustment), and calls one registered model (break_classifier, a trivial registered sklearn model) so AC-8's model dependency is real. Its traces flow to MLflow via @add_tracing and are exported to ACT. A validation job runs 20 prompts x 5 repetitions with an LLM judge and logs one MLflow run per repetition.
- **Acceptance check:** AC-8, AC-10.
- **Builds with:** domino-genai-tracing, domino-experiment-tracking (agent: domino-setup for tracing and tracking setup, builder for the agent and the validation job)
- **Verified by:** test-author

### 4.2 Pipeline

- **What:** two Domino jobs. jobs/intake.py runs the five intake adapters and upserts the inventory. jobs/validate.py runs the repeated-run evaluation for the Domino agent and posts the distribution to ACT. Both run as Domino jobs, not in the terminal. No Flows for the POC.
- **Acceptance check:** AC-1, AC-3 (re-tier on change), AC-10.
- **Builds with:** domino-jobs (agent: builder)
- **Verified by:** test-author

### 4.3 Governance collection

- **What:** one Domino Governance bundle per registered agent, created through the Governance Bundles API, with two policies attached: policies/act-2026-54.yaml (agentic: tier, manifest, identity, validation, monitoring, approvals, records) and policies/sr-26-2-model.yaml (a slim model-risk template for the model the agent calls). ACT attaches evidence as it is produced. That evidence is the tier reasoning, the manifest, drift findings, the validation record, and the evidence pack files. Approvals use the bundle's approval stages where the API allows; ACT enforces builder/approver separation itself and mirrors the result.
- **Acceptance check:** AC-7, AC-8, AC-9. If the Governance API cannot be driven from the app within the task's fix rounds, T-8b's fallback (local governance state plus a bundle report file) satisfies AC-7 and AC-9 with the bundle attachment marked TODO.
- **Builds with:** domino-governance, domino-experiment-tracking (registry) (agent: domino-setup for policy YAML and bundle discovery, builder for the sync code)
- **Verified by:** test-author, plus Nick for the pack content

### 4.4 Deployed endpoints

- **What:** break_classifier registered in the Domino Model Registry and served as a Domino model endpoint, so the Domino agent's model dependency is a real registry entry with a version.
- **Acceptance check:** endpoint returns a class for a test payload; AC-8 lists it.
- **Builds with:** domino-model-endpoints (agent: domino-deploy)
- **Verified by:** test-author

### 4.5 Frontier + local LLM access

- **What:** all LLM calls go through the Domino LLM Gateway by alias via one create_message() helper. sonnet for the reconciliation agent's reasoning and for the validation judge; haiku for the one-sentence drift explanation shown on a finding. No local model in the POC.
- **Prompt caching:** none. No call shares a fixed prefix over 512 tokens.
- **Acceptance check:** a gateway call succeeds for sonnet and haiku; the judge model alias and gateway-reported model id are stored with each validation run (AC-10).
- **Builds with:** domino-llm-gateway, domino-llm-access (agent: builder)
- **Verified by:** test-author

### 4.6 AI-powered application

- **What:** the ACT console. React (Vite) frontend plus FastAPI backend, the default; the builder checks briefly whether Streamlit would be faster for a four-day cut and reports, but the default stands unless Nick says otherwise, because the fleet table, the agent page, and the timeline benefit from components. Scaffold with /dominodatalab:domino-app-init react. Screens:
  - **Fleet:** every agent across the five sources; columns source, name, owner, tier badge, status, open findings, last seen; filters by source and tier; a "Register external agent" button (AC-11).
  - **Agent:** tier card with the seven criteria and reasoning; identity binding (issuer, principal id, or a red "no distinct identity" flag); permitted-action manifest (tools, data scopes, systems, value limits); dependency graph as a list (models with registry version, MCP servers, tools); the two policies on one bundle (AC-8); approvals panel (AC-9); findings list; runs list; "Generate evidence pack" (AC-7).
  - **Run timeline:** the reconstruction (AC-5), with tool calls outside the manifest highlighted (AC-6).
  - **Register external agent:** URL field, criteria questions, passport preview (AC-11).
- **Design source:** domino-app-guidelines skill (standard Domino look, loaded separately; fall back to the plugin's domino-ui-bootstrap). Borrow the information architecture, not the styling, from the products the research found strongest: Dataiku Agent Management's scan / certify / stop sequence (we do scan / certify only), ServiceNow's "AI Passport" idea for the external agent view, and Microsoft Foundry Control Plane's asset list columns.
- **State / persistence:** stateful. The registry, events, findings, approvals, and audit events persist across restarts. Follow Section 4.7. Tables are global (one fleet for the project); user_id is stamped on approvals, registrations, overrides, and audit events, not used to filter the fleet.
- **Acceptance check:** AC-5, AC-6, AC-7, AC-8, AC-9, AC-11, AC-12 through the proxy URL.
- **Builds with:** domino-apps, domino-app-guidelines (fallback domino-ui-bootstrap), Section 4.7 for storage (agent: domino-deploy for the scaffold and deployment, builder for routes and components)
- **Verified by:** ui-tester (Playwright, through the proxy URL), visual-reviewer, test-author for the backend

### 4.7 Storage pattern (self-contained)

One SQLite database for the project on the dataset, resolved from the Domino environment (/mnt/data/&lt;project&gt;/act/act.db for a git-based project, /domino/datasets/local/&lt;project&gt;/act/act.db otherwise, ACT_DB as an override, ./act.db locally). For this project that is `/mnt/data/ai-control-tower/act/act.db`. SQLAlchemy QueuePool with pool_size 20, max_overflow 5, pool_pre_ping off, a connect listener that sets journal_mode=WAL, foreign_keys=ON, busy_timeout=30000 once per pooled connection, and a pre-warm of 10 connections in the FastAPI lifespan (ACT_DB_PREWARM_COUNT=0 locally). init_db() runs create_all plus an idempotent, additive _migrate(); no Alembic. Caller identity comes from the proxy headers X-Domino-User-Id and X-Domino-User for browser traffic; the intake and validation jobs call the backend with a bearer token from localhost:8899/access-token and identify as the job's user via a verified Keycloak JWT (DOMINO_JWKS_URL); anonymous is rejected on every write route. Tables: agents, agent_versions, manifests, identities, dependencies, runs, events (raw span JSON, canonical event JSON, prev_hash, hash), findings, approvals, validation_records, evidence_packs, audit_events. The events hash is sha256(prev_hash || canonical_json); GET /api/audit/verify walks the chain.

## 5. Models to test

| Role in demo | Recommended model(s) | Frontier / local | Why |
|---|---|---|---|
| Reconciliation agent reasoning | sonnet via LLM Gateway | frontier | Tool use must be reliable for the demo and for a clean trace |
| Validation judge (AC-10) | sonnet via LLM Gateway | frontier | Judge version is recorded in the validation record; a weaker judge would be the demo's weak point |
| Drift explanation on a finding | haiku via LLM Gateway | frontier, cheap | One sentence, high volume, latency-sensitive |
| break_classifier | sklearn logistic regression, Domino endpoint | local | A real registry entry for the dependency graph; accuracy is irrelevant to the demo |

Cross-model validation: none in the POC. The validation record stores the judge alias and model id so a second judge can be added later.

## 6. Architecture & schematics

**Shape.** Five intake adapters implement one interface (adapters/base.py: `list_agents() -> list[SourceAgent]`, `get_agent(native_id) -> SourceAgent`, `list_runs(native_id, since) -> list[SourceRun]`). Four are replay adapters that read fixtures shaped like the real APIs: AWS AgentCore (ListAgentRuntimes and Agent Registry ListDiscoverableRegistryRecords responses), Google Agent Registry (Service and Binding resources), Microsoft Agent 365 (Graph "List packages" response), Databricks Unity Catalog (/api/2.1/unity-catalog/agent-services response). Each replay adapter has the real HTTP call written but gated behind ACT_LIVE_&lt;PLATFORM&gt;=1 and untested against a live account in this POC. The fifth adapter is live. It lists Domino Apps and Agents through the Domino API with the bearer token, reads agent versions and the MLflow experiment, and reads the agent's access events from the Audit Trail API. A normalize() step maps every SourceAgent into the Agent Record (A2A Agent Card fields, OASF tags, bank fields) and computes the ACT id as `act:<platform>:<sha256(native_id)[:16]>`.

**Trace path.** The Domino agent's MLflow traces are exported with MLflow's OTLP exporter and OTel GenAI semconv translation (MLFLOW_ENABLE_OTEL_GENAI_SEMCONV=true) to ACT's /v1/traces. Fixture runs for the other four sources are posted to the same endpoint by the intake job, in OTel GenAI, OpenInference, and MLflow attribute dialects, so the normalizer is exercised on all three. normalize_span() produces a canonical event (`{run_id, agent_id, ts, kind: input|model_call|tool_call|output|approval|override, name, args_redacted, attrs}`) and appends it to the hash chain. The drift detector runs on each completed run: observed_tools - manifest.tools, plus value-limit checks where the manifest declares one.

**Governance path.** On registration, ACT creates a bundle in Domino Governance with the two policies, then attaches evidence as it is produced. The evidence pack generator reads only ACT's own tables and renders Markdown, then PDF via WeasyPrint (or markdown-pdf if WeasyPrint's system deps are missing in the environment; the builder reports which).

**Interface contracts (write tests from these):**

- schemas/agent-record.json, schemas/canonical-event.json, schemas/finding.json, schemas/evidence-pack.json are the source of truth; routes validate against them.
- POST /v1/traces accepts OTLP/HTTP JSON and protobuf; returns 200 with `{"accepted": n, "agents_matched": m}`; unmatched gen_ai.agent.id values create a shadow_agent finding instead of being dropped.
- POST /api/agents/external takes `{card_url, criteria}`; fetches the card within 5 seconds or returns 504.
- POST /api/agents/{id}/approve requires a caller different from agents.registered_by; otherwise 403.
- POST /api/agents/{id}/evidence-pack returns `{markdown_path, pdf_path, bundle_id}`.

**Demo script (the validation walk).** Cold start the app. Fleet shows 13 agents across five sources (3 AgentCore, 3 Google, 3 Agent 365, 2 Databricks, 1 Domino, plus the external one once registered). Open recon-agent (Domino): tier 2, identity bound to the Domino service account, manifest with three tools, dependency on break_classifier v2, two policies on one bundle, validation record with a distribution. Approve it as a second user; show the expiry. Open treasury-sweep-agent (AgentCore fixture): tier 1, one open scope_drift finding from Saturday 02:14, click through to the timeline, see transfer_funds called with an amount above the manifest's limit and a tool list_credentials that is not in the manifest, read the one-line explanation. Generate the evidence pack; open the PDF; show the finding in the monitoring section. Register the external agent from the fixture card URL; show the passport. Total under ten minutes.

**Schematics to produce** (in docs/, Mermaid): an architecture and integration map (adapters, normalizer, store, governance sync, console); a sequence diagram for the trace path (agent, MLflow, OTLP exporter, ACT ingest, normalizer, hash chain, drift detector, finding); an ER diagram for the twelve tables.

**Borrow, don't reinvent.** Pull schemas and ideas from these, all open or documented: the A2A 1.0 Agent Card JSON schema (a2a-protocol.org) for the record's capability fields; AGNTCY OASF (docs.agntcy.org) for skill and domain taxonomy; OTel GenAI agent spans (open-telemetry/semantic-conventions-genai) and OpenInference for the span dialects; OCSF 1.8's ai_agent object for the canonical event's actor fields; MLflow 3 tracing and evaluation for the Domino agent; ValidMind's Atryum (Apache-2.0, github.com/validmind/atryum) for the shape of a per-action decision record; Microsoft's Agent Governance Toolkit (open source, April 2026) for any reusable runtime record types. Use a library only if it is pip-installable under Apache-2.0, MIT, or BSD; otherwise copy the schema and cite it in docs/BORROWED.md.

## 7. Best practices & cost guardrails (for the builder)

Tailored from the plugin's cost-and-practices reference.

**Model routing.** The orchestrator runs on Opus and plans; builders, testers and reviewers run on Sonnet. In-app, every LLM call goes through the Domino LLM Gateway via create_message() with an alias (sonnet, haiku); never instantiate an OpenAI or Anthropic client directly.

**Compute.** The intake job and the validation job run as Domino jobs, never in the terminal. Commit and push before launching either; a job runs against the remote. Small hardware tier for both. List /mnt/data/ once and record paths.

**Fixtures first.** Every adapter is built and tested against its fixture before any live flag is touched. The live Domino adapter is tested against the workspace's own project. Do not spend fix rounds on live hyperscaler calls.

**Non-negotiable Domino conventions.**

- Apps bind 0.0.0.0; port 9888 in a Workspace, 8888 as a deployed App; entry point app.sh; Vite `base: './'` and `allowedHosts: true`; jupyter-server-proxy installed; the app prints its access URL at startup.
- Code in /mnt/code; data under /mnt/data/ or /mnt/imported/data/.
- MLflow experiment names {base}-{project}-{username}.
- Host from $DOMINO_API_HOST; auth with a bearer token fetched from localhost:8899/access-token, sent as `Authorization: Bearer $TOKEN`. The X-Domino-Api-Key pattern is deprecated; do not copy it from skill examples.
- Never hardcode hosts or keys. Never mention Claude in the frontend UI.
- Render LLM output as markdown.

**How the build session runs:**

- domino-orchestrator runs on Opus and plans; builders and testers run on Sonnet, all in the one session. Each agent file pins its model, and the orchestrator passes sonnet when it calls the plugin's agents, which otherwise inherit Opus. No /model switching needed.
- Each task gets its own branch (task/&lt;id&gt;) and worktree (/mnt/code/.worktrees/&lt;id&gt;). Tasks whose dependencies are merged and whose files don't overlap run in parallel, up to three at once. Browser test runs take turns, since the workspace serves one app on port 9888.
- Every agent commits and pushes after each consistent change and before it reports, with the task id at the start of the message. Only the orchestrator merges into the integration branch.
- Each subagent starts with a fresh context and gets only the files its prompt names, so the orchestrator puts paths, AC text, and commands in every prompt.
- The plan and status live in build/plan.md and build/STATUS.md, so /clear on the orchestrator loses nothing. Clear between components.
- Reply within about five minutes when the orchestrator asks something, to keep the prompt cache warm. Add or remove MCP servers, or change effort, only right after a /clear.

**Develop until the checks go green:**

- Tests come first. test-author and ui-tester write each task's tests from the ACs and confirm they fail before a builder starts.
- The orchestrator loops builder, then tests, then fixes, until the task's tests pass. Three rounds per task, then it stops and asks.
- ACs that cannot become a command are marked manual in Section 8 and verified by a person.

**Cost monitor** (files in Appendix A):

- The status line shows estimated build spend against the Section 8 budget, plus session spend and context use.
- Once the estimate reaches the budget, new subagent calls are refused and the orchestrator writes build/STATUS.md and asks to continue. Raise budget_usd in build/budget.json to resume.
- Subagents cannot spawn subagents, and at most three run at once.
- Cumulative spend after each green task goes to the cost log in the QA dataset.

**Plugin extras:**

- Start each component from its command where one exists: /dominodatalab:domino-app-init react, /dominodatalab:domino-experiment-setup act, /dominodatalab:domino-trace-setup, and /dominodatalab:domino-debug-proxy to audit app plumbing.
- Switch on the Domino MLOps output style (/output-style) for the build.

**Budget allocation guidance for the orchestrator.** $1,900 across 13 tasks is about $145 per task on average. Wave 1 (scaffold, schemas, fixtures) should cost under $250 together. If spend passes $1,100 before T-9 (console) is green, drop T-11 and T-12 to build/STATUS.md as deferred and finish T-13.

## 8. Build team and QA

- **Build budget (USD):** 1900, written to build/budget.json.
- **Integration branch:** main. (Resolved 2026-11-09: origin's only branch is `main`.)
- **QA dataset folder:** /mnt/data/ai-control-tower/qa. (Resolved 2026-11-09: the Domino project is `ai-control-tower`, not `act`.)
- **UI ACs (ui-tester, Playwright):** AC-5, AC-6 (finding visible), AC-7, AC-8 (two policies visible), AC-11 (passport view), AC-12.
- **Code-level ACs (test-author):** AC-1, AC-2, AC-3, AC-4, AC-6 (finding created), AC-8 (dependency and bundle), AC-9, AC-10, AC-11 (fetch, verify, tier).
- **Manual ACs:** AC-7 content review (Nick); AC-10 judge sanity (Nick reads five judged samples); the Governance bundle attachment in AC-7 if T-8b's fallback is active (Nick confirms in the Domino UI).
- **Screenshot reuse:** demo video stills and a blueprint; keep the 1440x900 viewport.
- **Design source for visual-reviewer:** same as Section 4.6.

The roster, the per-task loop, and the file contents are in Appendix A.

## 9. Task list

Atomic, independently-shippable, in dependency order. The orchestrator runs tasks in parallel on separate branches when their dependencies are merged and their Touches lists don't overlap. Shared files belong to T-1.

- **T-1, scaffold the app and storage.** In: Section 4.6, 4.7, 7. Out: React (Vite) + FastAPI app from /dominodatalab:domino-app-init react, app.sh, requirements.txt, package.json, backend/db.py and backend/domino_identity.py per Section 4.7 with the twelve tables, backend/main.py with lifespan pre-warm, health route, .env.example, .gitignore. ACs: none directly (enables all). Depends on: none. Touches: app.sh, requirements.txt, package.json, vite.config.js, backend/db.py, backend/domino_identity.py, backend/main.py, frontend/ scaffold, .gitignore. Skill: domino-apps, domino-app-guidelines. Agent: domino-deploy. Verify: test-author (health route, DB init on a temp path, anonymous write rejected).
- **T-2, schemas and tiering config.** In: Section 3, 4.7, 6. Out: schemas/agent-record.json, schemas/canonical-event.json, schemas/finding.json, schemas/evidence-pack.json, config/tiering.yaml (seven criteria, weights, tier thresholds, the no-benchmark rule), config/redaction.yaml, backend/tiering.py, docs/BORROWED.md. ACs: AC-2, AC-3 (scorer). Depends on: none. Touches: schemas/, config/, backend/tiering.py, docs/BORROWED.md. Skill: none (pure Python). Agent: builder. Verify: test-author AC-2, AC-3.
- **T-3, fixtures for four hyperscaler feeds and the demo scenario.** In: Section 6 (shapes and counts), research file 01-hyperscaler-platforms.md for the API shapes. Out: fixtures/aws/, fixtures/google/, fixtures/microsoft/, fixtures/databricks/ (inventory responses, 11 agents total, with owners, tools, identities, and one agent per platform lacking a distinct identity), fixtures/runs/ (OTel GenAI, OpenInference, and MLflow-dialect span files, including weekend-drift.json for treasury-sweep-agent: 40 spans, transfer_funds over the limit at 2026-11-07T02:14:00Z and an out-of-manifest list_credentials call), fixtures/external/agent-card.json and a tiny fixtures/external/serve.py. ACs: AC-1, AC-12 (data). Depends on: T-2. Touches: fixtures/. Skill: none. Agent: builder. Verify: test-author (every fixture validates against its schema; counts match Section 6).
- **T-4, adapters and the intake job.** In: Section 6 adapter interface, T-3 fixtures. Out: adapters/base.py, adapters/aws.py, adapters/google.py, adapters/microsoft.py, adapters/databricks.py (replay by default, live call gated), adapters/domino.py (live: Apps and Agents list, versions, MLflow experiment, Audit Trail access events), backend/normalize.py, jobs/intake.py, routes GET /api/agents, GET /api/agents/{id}. ACs: AC-1, AC-2 (routes), AC-3 (re-tier on change). Depends on: T-1, T-2, T-3. Touches: adapters/, backend/normalize.py, backend/routes/agents.py, jobs/intake.py. Skill: domino-jobs, domino-python-sdk. Agent: builder. Verify: test-author AC-1, AC-2, AC-3.
- **T-5, trace ingest, canonical events, hash chain.** In: Section 6 trace path and contracts. Out: backend/otlp.py (JSON and protobuf), backend/normalize_span.py (three dialects), backend/chain.py, routes POST /v1/traces, GET /api/audit/verify, GET /api/runs/{run_id}/timeline, shadow_agent finding on unmatched agent ids. ACs: AC-4, AC-5 (backend). Depends on: T-1, T-2. Touches: backend/otlp.py, backend/normalize_span.py, backend/chain.py, backend/routes/traces.py, backend/routes/runs.py. Skill: none. Agent: builder. Verify: test-author AC-4, AC-5 (timing with a 500-span fixture).
- **T-6, drift detector and findings.** In: Section 6, T-5 events. Out: backend/drift.py (tool set difference, value limits), backend/routes/findings.py, severity from tier, one-line explanation via haiku through the gateway (backend/llm.py with create_message()). ACs: AC-6 (backend). Depends on: T-4, T-5. Touches: backend/drift.py, backend/llm.py, backend/routes/findings.py. Skill: domino-llm-gateway. Agent: builder. Verify: test-author AC-6.
- **T-7, the Domino-hosted reconciliation agent and the model endpoint.** In: Section 4.1, 4.4. Out: models/break_classifier/ (train script as a Domino job, registered in the Model Registry, served as an endpoint), agents/recon_agent/ (pydantic-ai or plain tool loop via the gateway, @add_tracing, deployed as a Domino Agent), MLflow OTLP export to ACT configured in the agent's environment. ACs: AC-8 (data), AC-10 (agent exists to validate). Depends on: T-1 (for the ACT ingest URL). Touches: models/, agents/, tracing_setup.py, experiment_setup.py. Skill: domino-genai-tracing, domino-experiment-tracking, domino-model-endpoints. Agent: domino-setup for tracing and experiment setup, domino-deploy for the endpoint and the agent deployment, builder for agent logic. Verify: test-author (endpoint responds; a run produces spans at ACT).
- **T-8, governance sync.** In: Section 4.3. Out: policies/act-2026-54.yaml, policies/sr-26-2-model.yaml, backend/governance.py (create bundle, attach policies, attach evidence, read approvals), backend/routes/approvals.py (builder/approver separation, expiry by tier), dependency route for AC-8. ACs: AC-8, AC-9. Depends on: T-4, T-7. Touches: policies/, backend/governance.py, backend/routes/approvals.py, backend/routes/dependencies.py. Skill: domino-governance. Agent: domino-setup for bundle discovery via the Domino MCP server, then builder. Verify: test-author AC-8, AC-9 (Governance API stubbed for the separation test, live for the bundle test).
- **T-8b, governance fallback (only if T-8 fails three rounds on the API).** Out: local governance state in approvals and evidence_packs, a bundle report Markdown under the dataset, and a TODO in build/STATUS.md. ACs: AC-7 and AC-9 with the attachment marked manual. Depends on: T-8 stopped. Touches: backend/governance_local.py. Agent: builder. Verify: test-author AC-9.
- **T-9, the console.** In: Section 4.6 screens, domino-app-guidelines. Out: frontend/src/pages/Fleet.jsx, Agent.jsx, RunTimeline.jsx, RegisterExternal.jsx, shared components, API client. ACs: AC-5 (UI), AC-6 (UI), AC-8 (UI), AC-12 (fleet and finding). Depends on: T-4, T-5, T-6, T-8 or T-8b. Touches: frontend/src/. Skill: domino-apps, domino-app-guidelines. Agent: builder (components), domino-deploy (proxy plumbing check with /dominodatalab:domino-debug-proxy). Verify: ui-tester and visual-reviewer AC-5, AC-6, AC-8, AC-12.
- **T-10, evidence pack generator.** In: Section 3 AC-7 order, schemas/evidence-pack.json. Out: backend/evidence.py (Markdown then PDF), route POST /api/agents/{id}/evidence-pack, attachment to the bundle via T-8 (or dataset path via T-8b), "Generate evidence pack" button and download in Agent.jsx. ACs: AC-7, AC-12 (pack includes the finding). Depends on: T-8 or T-8b, T-9. Touches: backend/evidence.py, backend/routes/evidence.py, frontend/src/components/EvidencePack.jsx. Skill: domino-governance. Agent: builder. Verify: ui-tester AC-7, AC-12; test-author (section order and citations); Nick for content.
- **T-11, validation job and record.** In: Section 4.1, AC-10. Out: jobs/validate.py (20 prompts x 5 reps, sonnet judge via gateway, one MLflow run per rep), backend/routes/validation.py, distribution in the record and the pack, eval/prompts.jsonl. ACs: AC-10. Depends on: T-7, T-10. Touches: jobs/validate.py, eval/, backend/routes/validation.py, backend/evidence.py (validation section only; coordinate with T-10 by merging T-10 first). Skill: domino-genai-tracing, domino-jobs, domino-llm-gateway. Agent: builder. Verify: test-author AC-10 (judge stubbed); Nick reads five judged samples.
- **T-12, client-agent intake (passport).** In: AC-11, fixture card and server from T-3. Out: backend/external.py (fetch card, JWS verify with jwcrypto or python-jose, tier from skills plus criteria), route POST /api/agents/external, RegisterExternal.jsx passport view. ACs: AC-11. Depends on: T-4, T-9. Touches: backend/external.py, backend/routes/external.py, frontend/src/pages/RegisterExternal.jsx. Skill: none. Agent: builder. Verify: test-author AC-11 (fetch, verify, tier); ui-tester AC-11 (passport view).
- **T-13, demo hardening and docs.** In: Section 6 demo script. Out: docs/architecture.md with the three Mermaid schematics, docs/DEMO.md (the ten-minute script with the exact clicks), jobs/seed_demo.py (one command from cold start to the demo state), README. ACs: AC-12 end to end, definition of done. Depends on: all merged. Touches: docs/, jobs/seed_demo.py, README.md. Agent: builder. Verify: ui-tester full suite; Nick walks the script.

Suggested waves: Wave 1 = T-1, T-2 (parallel). Wave 2 = T-3, T-5, T-7 (parallel). Wave 3 = T-4, T-6. Wave 4 = T-8, T-9 (parallel; T-9 starts against stubbed approvals). Wave 5 = T-10, T-12. Wave 6 = T-11, T-13.

## 10. Open questions

- ~~TODO: Domino project name and dataset name for /mnt/data/&lt;project&gt;/qa and the SQLite path. Assumed act.~~ Resolved 2026-11-09: project `ai-control-tower`, dataset `/mnt/data/ai-control-tower`.
- TODO: Does the build workspace's compute environment have Playwright and Chromium (Appendix A Dockerfile lines)? If not, add them to the environment before starting; the preflight will fail otherwise.
- TODO: Does the Domino Governance Bundles API in this deployment allow creating a bundle, attaching a policy, and uploading evidence from a service account? domino-setup confirms via the Domino MCP server in T-8 before builder writes governance.py. If not, T-8b.
- TODO: Is the LLM Gateway 2.0 deployed in this Domino instance with sonnet and haiku aliases? If the legacy gateway only, use whatever aliases exist and record them in build/STATUS.md.
- TODO: Is MLflow in this deployment at 3.11 or later (OTLP export with GenAI semconv)? If older, T-7 posts spans to ACT directly from the agent's tracing hook instead.
- TODO: Second Domino user for the AC-9 approval demo (the approver). Nick names one or creates a service account.

Deferred by design, not open: policy propagation and kill switch, live hyperscaler credentials, WORM storage, SIEM export, ServiceNow CMDB sync.

## Appendix A. Build team files

Copied from the plugin's build-team reference with the Section 8 values filled in (budget 1900, QA dataset /mnt/data/ai-control-tower/qa). The roster, branch and worktree rules, the per-task loop, Playwright, the preflight, and the cost monitor are as the reference describes. The files themselves live in the repo at the paths below; the repo copies are authoritative.

- .claude/settings.json (main session agent domino-orchestrator, subagent depth 1, concurrency 3, cost status line, Agent guard hook)
- build/budget.json (budget_usd 1900, warn_at 0.8, cost_log /mnt/data/ai-control-tower/qa/cost-log.csv)
- .claude/hooks/cost_monitor.py (statusline, guard, log &lt;phase&gt;)
- .claude/hooks/guard_paths.py (per-agent write path guard)
- build/qa/preflight.sh (plugin, Playwright, QA folder, budget, git push, .worktrees ignored)
- tests/e2e/conftest.py (1440x900 viewport, `shot` fixture writing to $QA_RUN_DIR)
- .claude/agents/domino-orchestrator.md, builder.md, test-author.md, ui-tester.md, visual-reviewer.md, code-reviewer.md

Example ui-tester test written against conftest.py:

```python
from playwright.sync_api import Page, expect


def test_ac3_summary_renders(page: Page, shot):
    page.goto("./")
    page.get_by_role("button", name="Summarize").click()
    expect(page.get_by_test_id("summary")).to_contain_text("Risk")
    shot("then")
```

### Compute environment additions (before the preflight)

```dockerfile
USER root
RUN pip install --no-cache-dir playwright pytest-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --with-deps chromium && chmod -R a+rX /opt/ms-playwright
USER ubuntu
```
