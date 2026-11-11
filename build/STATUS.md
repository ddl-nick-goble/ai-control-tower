# ACT build status

Updated: 2026-11-11 (session resume 3). Integration branch: `main` @ e76611a. Build spend about $112+ of $1,900 (see cost log).

## Wave W2 in progress (see build/plan.md "Combined schedule")
- T-39 Settings page: MERGED (311c986), post-merge 634 pass / 3 skip. Follow-ups queued as T-39f.
- T-19b Agent + Run pages: rebased onto main (64dfc8d), final code review approved with follow-ups (T-19f). UI run 4 (pre-merge, full suite) running, then design review r4, then merge.
- T-47 parallel e2e harness: H-1/H-2/H-5 done; H-3 (xdist) and H-4 (two suites at once) runs going.
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
