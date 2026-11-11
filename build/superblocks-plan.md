# Plan: Superblocks first principles in Agent Control Tower (ACT)

Status: APPROVED 2026-11-11 (Nick: "go ahead with the superblocks plan too"). T-27 and T-31 are replaced by build/import-wizard-plan.md (T-44, T-43). Written 2026-11-11.
Source: docs.superblocks.com (what-is-superblocks, building-with-clark, admin/org-administration, enterprise/deployment-overview, integrations/overview, admin/code-security). Pages on audit, versioning and environment promotion were not read; items that rely on them are marked (inferred).

## 1. The idea in one line

Superblocks lets business teams build apps with AI and lets IT govern them all from one place: apps never touch systems directly, governance is set once at the platform level, every action runs as a known identity, policy gates run before publish, and everything is audited. ACT applies the same idea to AI agents at a bank. Agents get built anywhere (AgentCore, Agent Platform, Agent 365, Databricks, Domino, or an external A2A agent). Risk governs all of them from one control tower, under OCC 2026-54.

Demo line: "Your teams can build agents on any platform. ACT is the one place risk sets the rules, sees every agent, and proves control to an examiner."

## 2. Principle to feature map (user's view)

| # | Superblocks principle | What the user sees today in ACT | What we add | Persona |
|---|---|---|---|---|
| P1 | One control layer. Apps reach business systems only through managed integrations, each created once ("Test Connection", "Create", "Share"). | Four fixture feeds and the Domino agent are pulled in by an intake job. Users can't see the sources. | **Connections** page: one card per source platform with status, last sync, agent count, a "Test connection" button, a "Sync now" button and a sync history. Sources are created once and every agent from them is inventoried. | Admin |
| P2 | Governed by default. A change made once applies to every app. | Tier weights live in config/tiering.yaml, approval expiry is in code, and drift severity follows the tier. None of it is visible in the UI. | **Policies** page: the org-wide rules in plain language (tier weights and thresholds, approval expiry by tier, required controls per tier, drift severity). Editing a rule shows an **impact preview** ("3 agents move to Tier 1; 2 approvals become due") before it is applied. Applying writes an audit event and re-tiers agents. | Risk lead / Admin |
| P3 | Identity-aware. The agent acts as the user, and you can only do what your role allows. | Agent page shows an identity binding or a red "no distinct identity" flag. | **Identity lens**: a Fleet filter and tile for "No distinct identity". The Agent page shows "Acts as" (the principal, its issuer, and the scopes it holds versus the scopes it declares), with any excess scopes flagged. | Risk officer |
| P4 | Centralized RBAC: org roles, resource roles, groups from the IdP. | Any signed-in Domino user can do everything except approve their own agent (AC-9). | **Roles**: Owner/Builder, Reviewer (approver), Examiner (read-only), Admin, mapped from Domino users (config/roles.yaml). The UI is role-aware: actions you can't take are disabled with the reason ("Only a Reviewer can approve"). A **Members** screen lists who holds which role. | Admin |
| P5 | Policy gates before publish. Checks run at install, publish and production. Critical or high findings block. "Publish anyway" is gated, and "Fix with Clark" offers a remedy. | Approve button. AC-9 enforces separation of duties and sets expiry. | **Approval gate**: before approval, a **Pre-approval checks** panel (card signature, identity binding, manifest present, validation record for Tier 1-2, no open critical findings, owner set). Failed critical checks block approval. "**Approve with exception**" needs a justification and an expiry, is recorded as an override, and appears in the evidence pack. Each failed check has a "How to fix" action (request identity binding from the owner, open the manifest, run validation). The checks run at three moments: **Intake**, **Approval** and **Runtime**, mirroring Superblocks' Development/Publish/Production. | Reviewer |
| P6 | Organization Knowledge: standards the builder follows by default. | Evidence pack sections follow the 2026-54 order. | **Standards** library: each 2026-54 and SR 26-2 requirement as a short card. Every check, policy and evidence-pack section links to the standard it serves, so an examiner can trace requirement → control → evidence. | Examiner |
| P7 | Checkpoints and versions; dev, staging and prod environments (inferred). | Records carry versions. A re-tier writes an audit event. | **Change history** on each agent: a version timeline with a diff (tools, scopes or model added or removed), what triggered it (intake run, policy change, registration), and the tier before and after. Any change that raises risk sends the agent back to Pending review. | Reviewer |
| P8 | Central audit: every query and API call is audited. | Events are hash-chained. Audit events exist but have no page. | **Audit log** page: one filterable stream (register, re-tier, approve, refuse, override, policy change, finding, evidence pack) with actor, time and object links, a "Chain verified" badge (AC-4 hash chain), and CSV export. | Examiner |
| P9 | Security Center: production monitoring and exceptions. | Findings per agent (scope drift). | **Findings center**: every finding across the fleet by severity, tier, owner and age, with triage actions (Acknowledge, Assign, Resolve with note, Accept risk until a date). Every action is audited. Each row links to the reconstructed timeline. | Risk officer |
| P10 | One place to start: onboarding, and a home for IT. | Fleet is the landing page. | **Home** (control-tower overview) for the signed-in role: "Needs you today" (pending reviews, approvals expiring within 30 days, open critical findings, agents with no identity), coverage by source, and a first-run **Getting started** checklist (connect sources, review policies, register your first external agent, run a review). | Everyone |
| P11 | Deployment matches compliance: control plane vs data plane; production data stays in the customer VPC. | Data stays in the Domino project dataset, with redaction config. | **Data residency** panel in Settings: where traces and evidence are stored (project dataset path, Governance bundle), the redaction rules that apply, and a statement that nothing leaves Domino. Mostly talk track, low build. | Security/IT |
| P12 | Bring apps from anywhere (Claude, Lovable, v0, code). | Register external agent by A2A card URL (AC-11). | Keep the URL flow. Add **Paste card JSON** and **Bulk import** (several card URLs) on the Register page. | Owner |

Out of scope: an AI builder like Clark. ACT doesn't build agents, it governs them. An optional "Explain this finding" feature through the LLM Gateway is listed under Could. It stays blocked until Nick accepts the gateway consent, and the UI never names the model vendor.

## 3. Proposed tasks (after T-19a and T-19b)

Each task follows the T-18 design system and the S-5/S-6 rules (tokens, AA contrast, keyboard focus, designed states, existing testids kept, frontend/dist rebuilt). Priority: **Must** = in the demo, **Should** = strengthens it, **Could** = if budget allows.

| Id | Title | Pri | Depends | Main files | Builder | QA |
|---|---|---|---|---|---|---|
| T-20 | Navigation and routes for the new areas (Home, Fleet, Findings, Policies, Connections, Audit, Register), with empty pages | Must | T-19b | frontend shell, router | builder | ui-tester, visual-reviewer, code-reviewer |
| T-21 | Audit log page + GET /api/audit (filters, paging, CSV, chain-verified) | Must | T-20 | backend/routes/audit.py, pages/Audit.jsx | builder | test-author, ui-tester, visual, code |
| T-22 | Approval gate: pre-approval checks API, blocking, approve with exception, override in the evidence pack | Must | T-20 | backend/gates.py, routes/approvals.py, Agent.jsx (approvals card), evidence.py | builder | all four |
| T-23 | Findings center: cross-fleet list, triage actions, audit events | Must | T-21 | routes/findings.py, pages/Findings.jsx | builder | all four |
| T-24 | Home: "Needs you today", coverage, Getting started | Must | T-21, T-22, T-23 | pages/Home.jsx, routes/summary.py | builder | ui-tester, visual, code |
| T-25 | Policies page with impact preview and apply (re-tier + audit) | Must | T-21 | routes/policies.py, tiering.py, pages/Policies.jsx | builder | all four |
| T-26 | Roles and role-aware UI, Members screen | Should | T-22 | domino_identity.py, config/roles.yaml, shared components | builder | all four |
| T-27 | Connections page: sources, test, sync now, history | Should | T-20 | routes/connections.py, pages/Connections.jsx, jobs/intake.py | builder (domino-setup for Job launch) | all four |
| T-28 | Agent change history with diff and re-review on risk increase | Should | T-22 | routes/agents.py, Agent.jsx history card | builder | all four |
| T-29 | Standards library + links from checks, policies, evidence | Should | T-22, T-25 | config/standards.yaml, pages/Standards.jsx | builder | test-author, ui, visual, code |
| T-30 | Identity lens (Fleet tile/filter, "Acts as" with excess scopes) | Should | T-20 | Fleet.jsx, Agent.jsx identity card | builder | ui, visual, code |
| T-31 | Register: paste card JSON, bulk import | Could | T-20 | Register.jsx, routes/external.py | builder | all four |
| T-32 | Data residency panel in Settings | Could | T-20 | pages/Settings.jsx | builder | ui, visual |
| T-33 | Republish the App from main, demo seed covering every new screen, walk-through | Must | all Musts | jobs/seed_demo.py, app | domino-deploy (sonnet) | ui-tester (demo walk) |

Waves. Tasks in the same wave touch different files.
- A: T-20
- B: T-21, T-22, T-27
- C: T-23, T-25, T-30
- D: T-24, T-26, T-28
- E: T-29, T-31, T-32
- F: T-33

Must-only path (smallest slam-dunk demo): T-20 → T-21, T-22 → T-23, T-25 → T-24 → T-33.

## 4. Acceptance criteria (draft, for the Must tasks)

- **SB-1 Audit (T-21).** Given any register, re-tier, approve, refused approval, override, policy change, finding triage or evidence-pack event, when it happens, then it appears on the Audit page within 2 seconds with actor (Domino user), time, action and a link to the object. The list filters by action, actor, agent and date. "Export CSV" downloads the filtered rows. A "Chain verified" badge reflects the AC-4 hash-chain check and turns red, naming the first broken link, if verification fails.
- **SB-2 Pre-approval checks (T-22).** Given an agent in Pending review, when a Reviewer opens it, then a checks panel lists signature, identity binding, manifest, validation (Tier 1-2), open critical findings and owner, each pass/fail with a reason and a "How to fix" action. Approve is disabled while any critical check fails. "Approve with exception" requires a justification of at least 20 characters and an exception expiry no later than the tier expiry. It records an override audit event, and the override appears in the evidence pack's "approvals and overrides" section. AC-9 separation still holds.
- **SB-3 Findings center (T-23).** Given open findings across agents, when the user opens Findings, then they see every finding with severity, tier, agent, owner, age and status, filterable and sortable. Acknowledge, Assign, Resolve (note required) and Accept risk (expiry required) each change the status, write an audit event, and show on the agent page. The weekend-drift finding links to its timeline.
- **SB-4 Policies (T-25).** Given the current rules, when an Admin changes a tier threshold or weight, then before saving an impact preview lists the agents whose tier would change (old → new) and the approvals that become due. "Apply" re-tiers them, writes one policy-change audit event plus one re-tier event per agent, and sends any agent whose tier rose back to Pending review. Non-admins see the rules read-only.
- **SB-5 Home (T-24).** Given the seeded demo, when a Reviewer lands on the app, then Home shows counts and lists for pending reviews, approvals expiring within 30 days, open critical findings and agents without a distinct identity. Each item opens its target. The Getting started checklist ticks items based on real state.
- **SB-6 Demo-ready (T-33).** The published Domino App on main shows every Must screen with seeded data. The demo walk in Section 5 runs end to end in the browser with screenshots, and no screen shows "unavailable", raw JSON or a status code.

The Should and Could ACs get written in the same style when those tasks are scheduled.

## 5. Demo script (about 8 minutes, risk officer)

1. **Home.** "Monday 9am. Here's what needs me: one Tier 1 agent drifted over the weekend, two reviews pending, one approval expiring." (P10)
2. **Connections.** Five platforms, one inventory, last synced minutes ago. "Teams build anywhere; we see everything." (P1)
3. **Findings center**, then the drift finding, then the **timeline**: the agent called a tool outside its manifest on Friday night, reconstructed in seconds. Triage it with Assign → owner and Accept risk? No: escalate. (P9)
4. **Agent page.** "Acts as" shows the agent's principal holds payments:write but declares only payments:read. Change history shows the tool was added in Thursday's intake and re-tiered it. (P3, P7)
5. **Register an external agent.** Live tier preview, signed card verified. It lands in Pending review. (P12)
6. **Approval gate.** The builder tries to approve their own agent and is refused (AC-9). The Reviewer sees the checks: identity binding is missing, so approval is blocked. They show "Approve with exception" with a justification, then cancel and request the fix instead. (P4, P5)
7. **Policies.** Raise the Tier 1 threshold sensitivity. The impact preview shows 2 agents moving to Tier 1. Apply. (P2)
8. **Audit log.** Every step just taken is there, chain verified, exported to CSV. (P8)
9. **Evidence pack.** Generate it. Every 2026-54 section cites its record and standard, and the exception and finding are included. It's attached to the Governance bundle. (P6)

## 6. Risks and open questions for Nick

1. **Scope versus budget.** The Must path is 7 tasks, the full plan 14. Check spend before committing; the plan.md budget is $1,900.
2. **Roles source.** Are roles mapped from a static config file of Domino usernames (simple, demo-safe) or from Domino organizations/groups (real, more work)? Proposed: a config file now, groups later.
3. **Second approver user** (still open from AC-9). The demo needs two Domino users, or demo mode's stand-in.
4. **Policy edits in a regulated demo.** Should "Apply" also attach the policy change to the Governance bundle? Proposed: yes, as evidence on each affected agent's bundle.
5. **LLM "Explain this finding"** stays out unless the LLM Gateway consent is accepted.
6. Superblocks pages on audit, versioning and environment promotion weren't read; P7 and P8 follow the stated principle, not a documented screen.

## 7. Decisions taken (2026-11-11, defaults until Nick says otherwise)
- Roles (T-26) are managed by admins on the in-app Settings page (T-39), which already holds the Admins list; Domino groups later.
- Applying a policy change attaches it as evidence to each affected agent's Governance bundle.
- "Explain this finding" (LLM) stays out until the LLM Gateway consent is accepted.
- Spend at approval: about $110 of $1,900.
