# Plan: Import wizard and connected sources (replace the manual Register flow)

Status: PROPOSED, waiting for Nick's go-ahead. Written 2026-11-11.
Replaces T-27 (Connections) and T-31 (paste JSON / bulk import) in build/superblocks-plan.md.

## 1. The problem (Nick, 2026-11-11)

"Register external agent" today means pasting one agent card URL by hand and scoring seven questions by hand. A risk team monitoring five platforms needs to **connect a platform once, sign in, let ACT crawl it, review what it found, and import in bulk**. After that, ACT should keep the inventory in sync on its own.

What already exists to build on:
- `adapters/` has one adapter per platform (AWS AgentCore, Google Agent Platform, Microsoft Agent 365, Databricks Unity Catalog, Domino) behind one contract (`list_agents`, `get_agent`, `list_runs`) that returns a rich `SourceAgent`: tools, data scopes, systems, value limits, identity, versions, and model/MCP dependencies.
- The four cloud adapters replay recorded fixtures. Their live HTTP code exists but is untested (`ACT_LIVE_<PLATFORM>=1`). The Domino adapter is live through the API proxy.
- `jobs/intake.py` runs every adapter at once and upserts the inventory. The UI has no way to trigger it, see it, or choose what to import.
- Re-tier on change (AC-3) and Pending review status (AC-11) already exist.

## 2. What the user experiences

### Fleet → "Import agents" (new primary action; "Add one agent by URL" stays as a secondary option)

**Step 1. Choose a system.**
Tiles for AWS Bedrock AgentCore, Google Agent Platform, Microsoft Agent 365, Databricks, Domino (this deployment) and "Other (A2A agent card URL)". Each tile shows whether it's connected already, when it last synced, and how many agents it has.

**Step 2. Connect and sign in** (only the first time; later imports skip to step 3).
Each platform shows its own sign-in form, with a short "what permissions we need" note and a copyable policy/role snippet:

| Platform | Auth method in the wizard | Read-only permission ACT asks for |
|---|---|---|
| AWS AgentCore | IAM role ARN + external ID (ACT assumes the role); access key as fallback | list/get agent runtimes and registry records, read IAM policies attached to the runtime role |
| Google Agent Platform | Service-account key JSON, or workload identity federation | list agent engines and their service accounts / IAM bindings |
| Microsoft Agent 365 | Entra app registration (tenant id, client id, secret), client-credentials flow | read agent inventory and app permissions via Graph |
| Databricks | Workspace URL + service principal (OAuth M2M) | list serving endpoints / agents, Unity Catalog grants |
| Domino | Nothing to enter: uses the app's Domino identity | list Apps/Agents, endpoints, registered models |
| Other | Agent card URL (today's flow) | — |

"Test connection" then shows what ACT could see, with green ticks per permission and plain-language fixes for anything missing ("Add `bedrock-agentcore:ListAgentRuntimes` to the role"). Secrets are encrypted at rest and never shown again; the user can re-authenticate or disconnect later.
- **Demo:** each cloud also offers "Use sample tenant", labelled as sample data, which uses the existing fixtures. The demo never pretends a sample tenant is live.

**Step 3. Choose what to crawl.**
Pick accounts/regions (AWS), projects (Google), tenants (Microsoft), workspaces/catalogs (Databricks), or projects (Domino). The default is everything the credential can see.

**Step 4. Crawl.**
A progress view: "Scanning us-east-1… 14 agents found, reading tools and permissions…". Per scope it shows a count and any errors, which don't stop the rest. The crawl runs as a **Domino Job** (data extraction never runs in the app process), so it survives the user closing the page, and its log is linked.

**Step 5. Review.**
A table of everything found, grouped as:
- **New**: not in ACT yet
- **Changed**: already in ACT, but tools, scopes or model changed, with a diff
- **Unchanged**
- **Missing**: in ACT but no longer found at the source

Each row shows the name, owner (mapped from source tags or creator to a Domino user, editable), identity (red flag if it has no distinct identity), tools and data scopes, and a **suggested tier**. The seven consequence scores are **pre-filled from evidence** (for example, a `payments:write` tool gives Financial exposure 4; no human-approval step gives Autonomy 4). Each score has a "why" tooltip and can be overridden; overrides need a one-line reason and are audited. Risky rows (Tier 1, no identity, write scopes, no owner) are highlighted. Users can select all, filter, or bulk-assign an owner.

**Step 6. Import.**
Selected agents are created in **Pending review**, each with a Governance bundle and an audit event ("imported from AWS us-east-1 by nick, connection X, crawl Y"). Changed agents are re-tiered, and go back to review if their risk went up. A summary screen shows "23 imported, 4 updated, 2 need an owner" with links.

**Step 7. Keep in sync.**
A choice of schedule (off / hourly / daily) runs the same crawl as a **scheduled Domino Job**. New agents land in an "Awaiting import" queue rather than being imported silently; changes and disappearances become findings for the risk officer.

### Connections page (Settings → Connections)
One card per connected system showing status, auth method, last test, last sync, agent count, schedule and sync history, with actions: Test, Sync now, Edit scope, Re-authenticate, Disconnect (disconnecting keeps the records and marks them "source disconnected").

## 3. Architecture

| Piece | Domino building block | Custom |
|---|---|---|
| Crawl and sync runs | **Domino Jobs** (started from the app through the Jobs API with the user's or app's identity), **scheduled Jobs** for ongoing sync | `jobs/discover.py` (one connection, one scope set, writes a discovery run) |
| Secrets | **Project secret variable** `ACT_SECRET_KEY` holds the encryption key | `connections.secret` encrypted with it (Fernet); never returned by any API |
| Storage | **Dataset** (existing SQLite DB) | new tables: `connections`, `discovery_runs`, `discovered_agents` (staging, with a diff against `agents`) |
| Inventory and review | existing records, Governance bundles, audit | import endpoint that turns staging rows into records |
| Adapters | Domino API proxy for the Domino adapter | contract extended with `auth_methods()`, `test_connection(creds) -> {permission: ok/missing, fix}`, `list_scopes(creds)`, `discover(creds, scopes, progress)` |
| Suggested scores | — | `config/criteria_rules.yaml` + `backend/suggest.py`: explainable rules from tools, scopes, value limits, approval steps and identity to the seven scores |

APIs (all behind sign-in; connect and import need the Admin or Owner role once roles exist):
`GET/POST/PATCH/DELETE /api/connections`, `POST /api/connections/{id}/test`, `GET /api/connections/{id}/scopes`, `POST /api/connections/{id}/discover`, `GET /api/discovery-runs/{id}` (progress + staged rows), `POST /api/discovery-runs/{id}/import`, `PUT /api/connections/{id}/schedule`.

## 4. Tasks

| Id | Title | Depends | Main files | Builder | QA |
|---|---|---|---|---|---|
| T-40 | Connections backend: tables, encrypted secrets, connection CRUD + test, adapter contract extension (test_connection, list_scopes, auth_methods) with sample-tenant mode | T-19a | backend/connections.py, backend/routes/connections.py, backend/db.py, adapters/base.py | builder | test-author, code-reviewer |
| T-42 | Suggested criteria engine with "why" per score; override needs a reason, audited | T-19a | config/criteria_rules.yaml, backend/suggest.py | builder | test-author, code-reviewer |
| T-41 | Discovery: jobs/discover.py as a Domino Job launched from the API, progress, staging + diff (new/changed/unchanged/missing), import endpoint (Pending review, bundles, audit, re-tier) | T-40, T-42 | jobs/discover.py, backend/discovery.py, backend/routes/discovery.py | domino-setup (sonnet) for the Job launch, then builder | test-author, code-reviewer |
| T-45 | Live adapters hardened per platform: real auth flows, scope listing, permission checks, contract tests against recorded HTTP; live Domino crawl of Agents, Apps, endpoints, models | T-40 | adapters/*.py, fixtures/http/ | builder | test-author, code-reviewer |
| T-43 | Import wizard UI (steps 1-6), "Add one agent by URL" kept as a path inside it, Fleet primary action | T-41, T-19b | frontend/src/pages/Import*.jsx, components | builder | ui-tester, visual-reviewer, code-reviewer |
| T-44 | Connections page + schedules (scheduled Domino Job) + sync history + "Awaiting import" queue + change/disappearance findings | T-43 | frontend/src/pages/Connections.jsx, backend/routes/connections.py (schedule), jobs/discover.py | domino-setup (sonnet) for scheduling, builder | all four |
| T-46 | Demo: seed connected sample tenants, a fresh "new agents found" crawl, walk-through screenshots; republish the App | T-44 | jobs/seed_demo.py, docs/DEMO.md | domino-deploy (sonnet) | ui-tester (demo walk) |

Waves (no file overlap within a wave): **A** T-40 + T-42 → **B** T-41 + T-45 → **C** T-43 → **D** T-44 → **E** T-46.
Merges with the Superblocks plan: T-43's wizard shares the shell from T-20. If both plans go ahead, T-20 runs first. T-44 replaces T-27, and T-43 replaces T-31.

## 5. Acceptance criteria (draft)

- **IW-1 Connect (T-40).** Given a platform and credentials, when the user saves a connection, then the secret is stored encrypted (absent from every API response, log and the DB in plaintext), and "Test connection" returns per-permission ok/missing with a plain-language fix. Bad credentials give a clear error, not a status code. A sample-tenant connection works with no credentials and is labelled "Sample data" everywhere it appears.
- **IW-2 Scopes (T-40/T-45).** Given a connection, when scopes are listed, then the user sees the accounts/regions/projects/workspaces the credential can read, with all selected by default.
- **IW-3 Crawl as a Job (T-41).** Given selected scopes, when the user starts a crawl, then a Domino Job starts (its id and link are shown), progress updates at least every 5 seconds with per-scope counts and errors, one failing scope doesn't stop the others, and closing the browser doesn't stop the crawl.
- **IW-4 Review diff (T-41).** Given a finished crawl, then each found agent is classed new/changed/unchanged/missing by (platform, native id), changed rows show a field-level diff (tools, scopes, model, identity), and a second crawl with no source changes produces no new or changed rows.
- **IW-5 Suggested scores (T-42).** Given a discovered agent, then all seven scores are pre-filled with a one-line reason each, citing the tool, scope or setting that drove it. An override requires a reason and writes an audit event. The same input always gives the same suggestion.
- **IW-6 Import (T-41).** Given selected rows, when the user imports, then each new agent is created in Pending review with its tier, owner, manifest, identity and dependencies, a Governance bundle and an "imported" audit event naming the user, connection and crawl. Changed agents are re-tiered (AC-3) and return to Pending review if their tier rose. The summary counts match the selection.
- **IW-7 Wizard UX (T-43).** Steps are shown with progress, Back keeps entered values, every step has designed loading/empty/error states, keyboard and screen-reader use works, there's no horizontal scroll at 1024 and 1440, and a connected platform skips straight to scope selection. The single-URL flow (AC-11) still works from inside the wizard.
- **IW-8 Sync (T-44).** Given a schedule, a scheduled Domino Job runs the crawl. New agents appear in "Awaiting import" (never auto-imported), changes and disappearances create findings, and the Connections page shows status, last sync, counts and history with Test, Sync now, Re-authenticate and Disconnect working.
- **IW-9 Live Domino (T-45).** Against this Domino deployment, the Domino connection lists the project's Agents/Apps, endpoints and registered models with real owners. Each cloud adapter passes contract tests against recorded HTTP for success, missing permission, throttling and pagination.

## 6. Decisions needed from Nick

1. **Real cloud accounts?** Do we have sandbox credentials for any of AWS, Google, Microsoft or Databricks? Without them, the clouds run on sample tenants in the demo, and live paths are proven only by recorded-HTTP contract tests. Domino is live either way.
2. **Who may connect sources and import?** Proposed: Admin (connect) and Owner/Reviewer (import). Until roles exist, any signed-in user, all audited.
3. **Secret storage.** Proposed: encrypted in ACT's DB with the key in a Domino Project secret variable. The alternative is the admin putting each credential in Project variables and the wizard only detecting them. That's simpler and safer, but there's no in-app sign-in.
4. **Suggested scores** for imported agents: should the reviewer confirm them in the wizard (proposed), or should they be taken as is and confirmed at approval?
5. **Order** relative to T-19b and the Superblocks plan. Proposed: T-19a → T-19b → this plan (it fixes the most visible demo gap) → the Superblocks Must tasks.
