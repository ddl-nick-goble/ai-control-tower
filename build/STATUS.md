# ACT build status

Updated: 2026-11-09 18:25 (workspace). Integration branch: `main`. Build spend about $56 of $1,900.

## Resume checklist (new session)
1. `bash build/qa/preflight.sh`. If Playwright or Node is missing after a workspace restart, reinstall (see plan.md "Environment facts"):
   - `pip install playwright pytest-playwright && pip install "packaging==23.2"`
   - `sudo env "PATH=$PATH" PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright python3 -m playwright install --with-deps chromium && sudo chmod -R a+rX /opt/ms-playwright`
   - Node 22: `curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs`
2. Read build/HANDOFF.md, build/plan.md (task table, design decisions, deferred items) and this file.
3. `git worktree list`, `git branch -r`. Resume the task under "In progress".

## Merged to main
T-1, T-2, T-3, T-4, T-5, T-5b (ingest fix), T-6, T-7, T-7L (classifier v1/v2 + endpoint), T-7L2 (input validation, champion v6), T-8, T-9, T-10, T-11, T-12 (b8aefb6), T-14 (a40b9e5).
Last post-merge result on main (after T-11, 7b0b627): 386 passed, 3 skipped (live-only).

## In progress
- **T-12** merged (b8aefb6). Post-merge on main: 424 backend passed, 33/33 e2e passed (QA dir wave6-main). Wave 6 complete.
- **T-14** merged (a40b9e5): mock LLM provider (ACT_LLM_PROVIDER), recon-agent hosted locally (ACT_DOMINO_LOCAL_AGENTS), in-process validation, docs/INTEGRATION.md. Post-merge backend run in progress.

## Next
- **T-14** (mocks and local hosting behind env switches; see plan.md "T-14"): started in parallel with T-12 (no file overlap); worktree .worktrees/T-14, branch task/T-14. Must merge before T-13.
- **T-13** builder RUNNING (rebased onto main, seed + docs + deferred polish). Test baseline 344eeee on task/T-13 (worktree .worktrees/T-13); the builder starts after T-14 merges (rebase onto main first). Scope: docs/architecture.md (3 Mermaid diagrams), docs/DEMO.md, jobs/seed_demo.py, README.
  - Seed demo: intake, the weekend-drift run, and POST /api/agents/{id}/governance for each agent before any approval.
  - Set ACT_EXTERNAL_ALLOW_HOSTS=127.0.0.1,localhost so the fixture card server works.
  - Deferred polish to apply, from plan.md: evidence-pack heading hierarchy, ARN wrapping, the fixtures/README line 8 wording, and the app.sh build step for a published App.
- After T-13: full suites (test-author + ui-tester) on main, then write qa-summary.md to /mnt/data/ai-control-tower/qa/ (pass/fail per AC with screenshot paths).

## Waiting on Nick
1. LLM Gateway consent: open https://apps.se-demo.domino.tech/apps/llmgateway/ once and accept. Then set the Project env var `LLM_GATEWAY_URL=https://apps.se-demo.domino.tech/apps/llmgateway`. This blocks the live recon-agent, the live haiku explanations, and the T-11 live validation job.
2. Service account to publish recon-agent (e.g. `svc-act-recon`, listed in ACT_SERVICE_ACCOUNTS), or accept recon-agent shown as bound to the publisher with "no distinct identity".
3. A second Domino user to approve recon-agent (AC-9 demo). Whoever runs intake cannot approve.
4. Demo wording: "break_classifier v2" should become "the champion version" (now v6, because of the int64 schema fix).
5. Optional: update the endpoint description in Domino, which still says "registry v2".

## Live items pending after consent
- Deploy recon-agent as a Domino Agent (domino-deploy) with RECON_AGENT_ID equal to its Domino app id. Env: ACT_TRACES_URL, BREAK_CLASSIFIER_ENDPOINT_URL, BREAK_CLASSIFIER_ACCESS_KEY (Project var), LLM_GATEWAY_URL.
- Run the validation job: `python jobs/validate.py` as a Domino Job (small tier). Nick reads the five judged samples in MLflow.
