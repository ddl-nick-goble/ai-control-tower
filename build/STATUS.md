# ACT build status

Updated: 2026-11-10. Integration branch: `main` @ 3570c5e. Build spend about $82 of $1,900.

## BUILD COMPLETE

Every task is merged to `main`: T-1 to T-13, plus T-5b, T-7L, T-7L2 and T-14. No task is in progress and only the main worktree remains. The task/* branches are kept on the remote for history.

Final results on `main`:
- 478 backend tests passed. 3 were skipped because they need live credentials or services: BREAK_CLASSIFIER_ACCESS_KEY, ACT_LIVE_URL and ACT_GOVERNANCE_LIVE.
- 41 of 41 browser tests passed.

QA summary with pass or fail per AC: /mnt/data/ai-control-tower/qa/qa-summary.md
Demo walk (9 of 9 steps OK): /mnt/data/ai-control-tower/qa/T-13-demo/

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
