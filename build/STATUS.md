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
T-1, T-2, T-3, T-4, T-5, T-5b (ingest fix), T-6, T-7, T-7L (classifier v1/v2 + endpoint), T-7L2 (input validation, champion v6), T-8, T-9, T-10, T-11.
Last post-merge result on main (after T-11, 7b0b627): 386 passed, 3 skipped (live-only).

## In progress
- **T-12** (external agent intake + passport), round 3 of 3, worktree `.worktrees/T-12`, branch `task/T-12`.
  - Backend is done and reviewed: SSRF guard with a pinned IP and non-global addresses blocked; 5 s total deadline; JWS verify.
  - Round-3 builder is rebasing onto main and then fixing UI items: human-readable status, a card-asserted identity panel for external agents, `data-signature` with a warning style for invalid signatures, user-facing error text, styled inputs, and blocking `::/96`.
  - Round-3 UI test baseline: 14f40d6, the pre-rebase commit. T-12's own test diff before the rebase is saved in build/review/T-12-tests-prerebase.diff. After the rebase, compare it with `git diff $(git merge-base origin/main origin/task/T-12) origin/task/T-12 -- tests/`.
  - Next steps:
    1. ui-tester runs the full e2e suite on T-12.
    2. visual-reviewer and code-reviewer run.
    3. Merge. Post-merge tests: backend plus the full e2e suite (end of Wave 6).
  - If round 3 is not green, stop and ask Nick (3-round rule).

## Next
- **T-14** (mocks and local hosting behind env switches; see plan.md "T-14"): started in parallel with T-12 (no file overlap); worktree .worktrees/T-14, branch task/T-14. Must merge before T-13.
- **T-13** (demo hardening and docs): docs/architecture.md (3 Mermaid diagrams), docs/DEMO.md, jobs/seed_demo.py, README.
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
