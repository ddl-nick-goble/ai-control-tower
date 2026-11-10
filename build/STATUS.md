# ACT build status

Updated: 2026-11-10. Integration branch: `main` @ 3570c5e. Build spend about $82 of $1,900.

## BUILD COMPLETE

Every task is merged to `main`: T-1 to T-13, plus T-5b, T-7L, T-7L2 and T-14. No task is in progress and only the main worktree remains. The task/* branches are kept on the remote for history.

Final results on `main`:
- 478 backend tests passed. 3 were skipped because they need live credentials or services: BREAK_CLASSIFIER_ACCESS_KEY, ACT_LIVE_URL and ACT_GOVERNANCE_LIVE.
- 41 of 41 browser tests passed.

QA summary with pass or fail per AC: /mnt/data/ai-control-tower/qa/qa-summary.md
Demo walk (9 of 9 steps OK): /mnt/data/ai-control-tower/qa/T-13-demo/

## T-15 demo mode (STOPPED after 3 rounds; decision needed)
Branch task/T-15 @ 3492bed, worktree .worktrees/T-15. D-1..D-11 pass: 17/17 demo backend tests, 495 backend passed (3 live-only skips), 9/9 demo e2e, 50/50 full e2e. Visual fixes done.
One blocking review item is open: demo auto-seed will claim any EMPTY database it is pointed at (e.g. ACT_DEMO_MODE=1 ACT_DB=<a fresh real DB>), after which "Reset demo" can wipe it. The default demo DB path is safe; the risk only applies when ACT_DB is set by hand. Fix is small (claim only DBs the demo created itself, or require ACT_DEMO_ALLOW_DB=1), plus two should-fix items (see plan.md).
Options: (a) one more builder round for these, then merge; (b) merge as is and log the item.

## Waiting on Nick (live and manual steps; not build work)
1. LLM Gateway consent: open https://apps.se-demo.domino.tech/apps/llmgateway/ once and accept. Then set the Project env vars `LLM_GATEWAY_URL=https://apps.se-demo.domino.tech/apps/llmgateway` and `ACT_LLM_PROVIDER=gateway`.
2. Deploy recon-agent as a Domino Agent and set RECON_AGENT_ID to its app id. Then remove its entry from config/local_agents.json and the local act:domino row.
3. Provide a service account for recon-agent (ACT_SERVICE_ACCOUNTS) and a real second Domino user to approve it.
4. Rerun `python jobs/validate.py` as a Domino Job with the gateway judge, then read the five judged samples in MLflow.
5. Sign off on the evidence pack content and arrange the MRM colleague review.
6. Optional: update the endpoint description, which still says "registry v2" (the champion is v6). Delete /mnt/data/ai-control-tower/act/act.db.stale-202611100020 if it is not needed.

docs/INTEGRATION.md maps each mock or local switch to its go-live step.

## If the workspace restarts
Playwright and Node were installed into the running workspace, not into the environment image. To reinstall them:
- `pip install playwright pytest-playwright && pip install "packaging==23.2"`
- `sudo env "PATH=$PATH" PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright python3 -m playwright install --with-deps chromium && sudo chmod -R a+rX /opt/ms-playwright`
- `curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs`
