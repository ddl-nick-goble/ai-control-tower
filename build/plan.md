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
| T-7 | AC-8 (data), AC-10 (agent) | T-1 | models/, agents/, tracing_setup.py, experiment_setup.py | domino-setup (sonnet) + domino-deploy (sonnet) + builder | test-author, code-reviewer | task/T-7 | 0c7f306 | building (round 2) |
| T-4 | AC-1, AC-2 (routes), AC-3 (re-tier) | T-1, T-2, T-3 | adapters/, backend/normalize.py, backend/routes/agents.py, jobs/intake.py | builder | test-author, code-reviewer | task/T-4 | 2c5fb61 | building |
| T-6 | AC-6 (backend) | T-4, T-5 | backend/drift.py, backend/llm.py, backend/routes/findings.py | builder | test-author, code-reviewer | task/T-6 | — | pending |
| T-8 | AC-8, AC-9 | T-4, T-7 | policies/, backend/governance.py, backend/routes/approvals.py, backend/routes/dependencies.py, backend/routes/runs.py (merge approvals and overrides into the timeline) | domino-setup (sonnet) then builder | test-author, code-reviewer | task/T-8 | — | pending |
| T-8b | AC-7, AC-9 (attachment manual) | T-8 stopped | backend/governance_local.py | builder | test-author | task/T-8b | — | conditional |
| T-9 | AC-5 UI, AC-6 UI, AC-8 UI, AC-12 | T-4, T-5, T-6, T-8/T-8b | frontend/src/ | builder + domino-deploy (sonnet, proxy check) | ui-tester, visual-reviewer, code-reviewer | task/T-9 | — | pending |
| T-10 | AC-7, AC-12 (pack) | T-8/T-8b, T-9 | backend/evidence.py, backend/routes/evidence.py, frontend/src/components/EvidencePack.jsx | builder | ui-tester, test-author, visual-reviewer, code-reviewer | task/T-10 | — | pending |
| T-12 | AC-11 | T-4, T-9 | backend/external.py, backend/routes/external.py, frontend/src/pages/RegisterExternal.jsx | builder | test-author, ui-tester, visual-reviewer, code-reviewer | task/T-12 | — | pending |
| T-11 | AC-10 | T-7, T-10 | jobs/validate.py, eval/, backend/routes/validation.py, backend/evidence.py (validation section) | builder | test-author, code-reviewer | task/T-11 | — | pending |
| T-13 | AC-12 e2e, DoD | all | docs/, jobs/seed_demo.py, README.md | builder | ui-tester (full), test-author (full) | task/T-13 | — | pending |

Shared registration of routers in backend/main.py belongs to T-1. Later tasks that need a router mounted must either use an auto-include pattern that T-1 provides or report the needed change.

## Design decisions
- Shadow agents (T-5, round 2): an unmatched gen_ai.agent.id creates a placeholder agents row with status `shadow` and id `act:shadow:<sha256(native)[:16]>`. The shadow_agent finding points to that placeholder. T-4's fleet route and T-9's fleet page show shadow agents separately, so the demo's 13-agent count holds.
- Drift dedupe (T-6): ingest is idempotent per (run_id, span_id). The run-ingested hook can fire more than once per run, so T-6 must upsert one scope_drift finding per run and keep it idempotent.
- For T-12 (from T-3 review): the signed card's signature covers base64url(protected) + "." + base64url(key-sorted, compact JSON of the card without `signatures`), ES256, public JWK at fixtures/external/signing-key.pub.jwk.json. The verifier must match tests/test_t3_signed_card.py. fixtures/README.md line 8 still says gen_ai.agent.id is the AWS agentRuntimeId; reword it to AWS-only when docs are touched (T-13).
