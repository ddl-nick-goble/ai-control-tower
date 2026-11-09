# ACT build plan

Source: build/HANDOFF.md. Integration branch: `main`. QA folder: `/mnt/data/ai-control-tower/qa`.
SQLite path for the deployed app: `/mnt/data/ai-control-tower/act/act.db` (git-based project).
Budget: $1,900. If spend passes $1,100 before T-9 is green, defer T-11 and T-12 and finish T-13.

## Environment facts (2026-11-09)
- Playwright and Chromium are installed in the running workspace with sudo (`PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`). This is not in the compute environment, so reinstall after a workspace restart. `packaging` is pinned back to 23.2 for dominodatalab and mlflow.
- Node 22 + npm 10 installed in the workspace with sudo from NodeSource (not in the compute environment, so reinstall after a restart). A deployed App needs node in its environment, or a committed frontend/dist.
- MLflow 3.3.2 (< 3.11): T-7 posts spans to ACT `/v1/traces` directly from the agent's tracing hook (handoff fallback).
- LLM Gateway: the "LLM Gateway" App at apps.se-demo.domino.tech/apps/llmgateway is Running (probably Gateway 2.0); there is no legacy AI Gateway. BLOCKED on consent: every route redirects to /app-consent until Nick opens the URL once and accepts, and LLM_GATEWAY_URL needs to be set as a Project env var. Aliases are unconfirmed.
- Model registry reachable through MLflow; model name break_classifier with alias champion.
- Open: LLM Gateway aliases (needed by T-6, T-7 live), Governance API from a service account (T-8), second approver user (AC-9 demo).

## Waves
A task joins a wave once its dependencies are merged and its Touches list does not overlap another task in the same wave.

| Wave | Tasks |
|------|-------|
| 1 | T-1, T-2 |
| 2 | T-3, T-5, T-7 |
| 3 | T-4 |
| 4 | T-6, T-8 (T-8b only if T-8 stops) |
| 5 | T-9 |
| 6 | T-10, T-12 |
| 7 | T-11 |
| 8 | T-13 |

Note: the handoff suggests T-4+T-6, T-8+T-9, and T-11+T-13 as pairs. Each pair has a dependency between its two tasks, so they run in consecutive waves.

## Tasks

| Id | ACs | Depends on | Touches | Builder | QA | Branch | Test baseline | Status |
|----|-----|-----------|---------|---------|----|--------|---------------|--------|
| T-1 | enables all (health, DB init, anon write rejected) | — | app.sh, requirements.txt, package.json, vite.config.js, backend/db.py, backend/domino_identity.py, backend/main.py, frontend/ scaffold, .gitignore, .env.example | domino-deploy (sonnet) | test-author, code-reviewer | task/T-1 | a14d50d | merged (822a54b) |
| T-2 | AC-2, AC-3 (scorer) | — | schemas/, config/, backend/tiering.py, docs/BORROWED.md | builder | test-author, code-reviewer | task/T-2 | 4a50153 | merged (8998f25) |
| T-3 | AC-1, AC-12 (data) | T-2 | fixtures/ | builder | test-author, code-reviewer | task/T-3 | fe4c551 | merged (b481c45) |
| T-5 | AC-4, AC-5 (backend) | T-1, T-2 | backend/otlp.py, backend/normalize_span.py, backend/chain.py, backend/routes/traces.py, backend/routes/runs.py, backend/db.py (findings columns, granted in Wave 2) | builder | test-author, code-reviewer | task/T-5 | 446af59 | merged (52b4ee0) |
| T-7 | AC-8 (data), AC-10 (agent) | T-1 | models/, agents/, tracing_setup.py, experiment_setup.py | domino-setup (sonnet) + domino-deploy (sonnet) + builder | test-author, code-reviewer | task/T-7 | 0c7f306 | merged (1291556); live part in T-7L |
| T-4 | AC-1, AC-2 (routes), AC-3 (re-tier) | T-1, T-2, T-3 | adapters/, backend/normalize.py, backend/routes/agents.py, jobs/intake.py | builder | test-author, code-reviewer | task/T-4 | 8852de3 | merged (be508f3) |
| T-6 | AC-6 (backend) | T-4, T-5 | backend/drift.py, backend/llm.py, backend/routes/findings.py, schemas/finding.json (additive reason value_unverifiable, granted in round 2), backend/db.py (unique index) | builder | test-author, code-reviewer | task/T-6 | 73fd715 | merged (c83df81) |
| T-8 | AC-8, AC-9 | T-4, T-7 | policies/, backend/governance.py, backend/routes/approvals.py, backend/routes/dependencies.py, backend/routes/runs.py (merge approvals and overrides into the timeline), backend/normalize.py + backend/routes/agents.py (registered_by only, granted in round 2) | domino-setup (sonnet) then builder | test-author, code-reviewer | task/T-8 | 6f65e19 | merged (fe7e4b8) |
| T-8b | AC-7, AC-9 (attachment manual) | T-8 stopped | backend/governance_local.py | builder | test-author | task/T-8b | — | conditional |
| T-9 | AC-5 UI, AC-6 UI, AC-8 UI, AC-12 | T-4, T-5, T-6, T-8/T-8b | frontend/src/ | builder + domino-deploy (sonnet, proxy check) | ui-tester, visual-reviewer, code-reviewer | task/T-9 | 50dcd2f | merged (8ee5258) |
| T-10 | AC-7, AC-12 (pack) | T-8/T-8b, T-9 | backend/evidence.py, backend/routes/evidence.py, frontend/src/components/EvidencePack.jsx, frontend/src/pages/Agent.jsx (evidence-pack slot and runs card), backend/routes/runs.py (GET /api/agents/{id}/runs, granted in round 2) | builder | ui-tester, test-author, visual-reviewer, code-reviewer | task/T-10 | b342075 | merged (0ef9892) |
| T-12 | AC-11 | T-4, T-9 | backend/external.py, backend/routes/external.py, frontend/src/pages/Register.jsx (replaces the T-9 stub) | builder | test-author, ui-tester, visual-reviewer, code-reviewer | task/T-12 | db6641c | building (round 2, backend) |
| T-11 | AC-10 | T-7, T-10 | jobs/validate.py, eval/, backend/routes/validation.py, backend/evidence.py (validation section) | builder | test-author, code-reviewer | task/T-11 | 08b23ce | building |
| T-13 | AC-12 e2e, DoD | all | docs/, jobs/seed_demo.py, README.md | builder | ui-tester (full), test-author (full) | task/T-13 | — | pending |

Shared registration of routers in backend/main.py belongs to T-1. Later tasks that need a router mounted must either use an auto-include pattern that T-1 provides or report the needed change.

## Design decisions
- Shadow agents (T-5, round 2): an unmatched gen_ai.agent.id creates a placeholder agents row with status `shadow` and id `act:shadow:<sha256(native)[:16]>`. The shadow_agent finding points to that placeholder. T-4's fleet route and T-9's fleet page show shadow agents separately, so the demo's 13-agent count holds.
- Drift dedupe (T-6): ingest is idempotent per (run_id, span_id). The run-ingested hook can fire more than once per run, so T-6 must upsert one scope_drift finding per run and keep it idempotent.
- For T-12 (from T-3 review): the signed card's signature covers base64url(protected) + "." + base64url(key-sorted, compact JSON of the card without `signatures`), ES256, public JWK at fixtures/external/signing-key.pub.jwk.json. The verifier must match tests/test_t3_signed_card.py. fixtures/README.md line 8 still says gen_ai.agent.id is the AWS agentRuntimeId; reword it to AWS-only when docs are touched (T-13).

## T-7L (live follow-up to T-7)
Train and register break_classifier as a Domino job, publish the endpoint, and record the endpoint URL plus access-key variable names. Then deploy recon-agent as a Domino Agent once the gateway consent is done. Branch task/T-7L. Builder: domino-deploy (sonnet). Review notes to apply: return the stored result if tracing fails after fn completes; retry act_export only on 5xx and network errors; make sure the agent's working directory or sys.path includes the repo root.
- T-7L live state (2026-11-09): break_classifier v1 and v2 are registered, alias champion points to v2, experiment break-classifier-ai-control-tower-nick_goble. The sync endpoint id is 6af239a468f40b2f5611dd6c, URL https://se-demo.domino.tech/models/6af239a468f40b2f5611dd6c/latest/model, Running; a test call returned amount_mismatch. The access key is the Project env var BREAK_CLASSIFIER_ACCESS_KEY. recon-agent is not deployed yet (it waits on gateway consent and the service-account decision).
- Governance API (T-8 discovery, 2026-11-09): bundles, policies, evidence answers, approvals, and stage moves all work as nick_goble through DOMINO_API_PROXY. Published policies: ACT 2026-54 Agentic Governance dbda3f71-df98-44fb-84f2-a7dcb14b8916 and SR 26-2 Model Risk (slim) 8341eac0-8430-4d17-980c-e9af89cdaa83, approver org model-gov-org. Report attachments only reference git paths and commits (no upload). The API does not enforce approver separation, so ACT enforces it and mirrors the result. T-8b is not needed. Untested: a service-account token.
- Drift evasion (T-6 round 2): amounts are normalised (thousands separators and currency symbols stripped). Non-finite or unparseable amounts, and currency mismatches, on a limited tool become a violation with reason `value_unverifiable` (an additive schema enum value) that keeps the raw value. A crafted amount must never suppress a finding.
- Approvals (T-8 round 2): registered_by is the user who first brings an agent in (intake caller or external registrant) and is never overwritten. Approval works only from status discovered or pending_review, refuses (409) when registered_by is NULL, and compares id and username ignoring case and whitespace. GET /governance is read-only and POST /governance creates the bundle. The Governance mirror runs in the background. Demo consequence: whoever runs intake cannot approve recon-agent; a second user must.
- T-7L2 merged (456548e). Input validation is in place; champion now points to v6, served by endpoint version 5; a missing feature returns 400. Earlier note: the endpoint 400 came from MLflow refusing int64 against a double signature. The fix logs a pyfunc wrapper that casts to float. break_classifier v5 is now champion and the endpoint serves v5; v3 and v4 were intermediates, and v1 and v2 are untouched. ASK NICK: the demo script says "break_classifier v2"; proposed wording is v5 or "the champion version". Domino jobs have no pytest; prefix job commands with `pip install -q --user pytest;`.
- Governance mirror (T-8 round 2): the approval mirror uses the stored bundle_id only. T-13 seed_demo must POST /api/agents/{id}/governance for each agent before any approval.
- T-6 hardening deferred (review suggestions, not blocking): compare amounts with Decimal; warn when a stored limit is unparseable; amount key lookup is case-sensitive and not nested; a bare limit ignores currency.
- Design source (T-9 onward): the domino-app-guidelines skill is not installed in this workspace, so per the handoff's fallback the console uses dominodatalab:domino-ui-bootstrap. visual-reviewer judges against the same skill.
- UI test seeding: tests/e2e/seed.py builds a demo DB in-process (intake, a recon-agent row with both policies and the break_classifier dependency, weekend-drift ingested) and the app starts with ACT_DB pointing at it. Only one ui-tester runs at a time (port 9888).
- External card fetch (T-12 round 2): resolved addresses that are loopback, private, link-local, unique-local or unspecified are rejected (422) and the connection is pinned to the checked IP. ACT_EXTERNAL_ALLOW_HOSTS (empty by default) admits the localhost fixture server in tests and demos. Port 8899 and 169.254.0.0/16 are always rejected. One 5 s wall-clock deadline covers the whole fetch, and any stall returns 504. DEMO NOTE: the T-13 demo serves the fixture card on localhost, so its app environment must set ACT_EXTERNAL_ALLOW_HOSTS=127.0.0.1,localhost.
- Evidence pack (T-10 round 2): no fake Report attachments. Use submit-result only; on failure, keep the files in the dataset and write an audit outcome. The pack never claims an attachment happened. Agent-controlled text is escaped and the PDF renderer fetches nothing. The dataset-relative location is recorded. A runs-by-agent route was added so the agent page and the pack list every run.

## T-5b (ingest fix, found during the T-10 review)
Only the agent-level span carries gen_ai.agent.id, so child spans (execute_tool, chat) were stored with agent_id "unknown": 39 of 40 in weekend-drift. That breaks AC-4 ("each span ... linked to the ACT agent id") and every per-agent count. Fix: a span without its own agent id inherits the agent of its run (same traceId) from the batch or from events already stored; once the run's agent is resolved, backfill earlier "unknown" rows of that run. Touches backend/routes/traces.py and backend/normalize_span.py. Builder: builder. QA: test-author, code-reviewer. Branch task/T-5b. Test baseline 3d6a8f0, updated to 0d88e27 after the setup fix (fresh trace ids). Split-batch re-links update only the events.agent_id column; hashed canonical JSON is never rewritten.
- Deferred to T-13 polish (from the T-10 visual review): the evidence-pack preview's section headings are small grey caps and need a stronger hierarchy; the identity ARN wraps mid-string. Note: the audit record for a failed evidence attachment stores only the outcome; the error detail is in the app logs.
