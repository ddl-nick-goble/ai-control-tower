# ACT build status

Updated: 2026-11-11. Integration branch: `main` @ dae7aa4. Build spend about $112 of $1,900.

## Wave W2 in progress (see build/plan.md "Combined schedule")
- T-19b Agent + Run pages: tests at baseline b98ed39 (27 fail as expected). Design brief (visual-reviewer) then builder.
- T-39 Settings page: builder round 0 pushed a75c895. QA: backend tests, browser tests, visual review, code review.
- Next: W3 T-20 (navigation + routes for the Superblocks screens).

Standing rule from Nick: a design agent (visual-reviewer) produces a design brief before and a review after every frontend build.

## Waiting on Nick (live and manual steps; not build work)
1. LLM Gateway consent: open https://apps.se-demo.domino.tech/apps/llmgateway/ once and accept, then set `LLM_GATEWAY_URL` and `ACT_LLM_PROVIDER=gateway` (or enter them on the Settings page once T-39 merges).
2. Deploy recon-agent as a Domino Agent and set RECON_AGENT_ID.
3. A service account for recon-agent (ACT_SERVICE_ACCOUNTS) and a second Domino user to approve it.
4. Rerun `python jobs/validate.py` as a Domino Job with the gateway judge.
5. Sign off on the evidence pack and arrange the MRM review.

## If the workspace restarts
Worktrees under .worktrees/ are lost; recreate from origin/task/<id>. Playwright + Node are in the project default environment ai-control-tower-dse-pw.
