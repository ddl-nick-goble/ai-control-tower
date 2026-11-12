# ACT build status

Updated: 2026-11-12 (session resume 5, later). Integration branch: `main` @ a35afbc. Build spend about $132 of $1,900 (see cost log).

## Why the workspace kept crashing (fixed 2026-11-12)
The workspace is on hardware tier **Small: 1 core, 4 GiB**. Each resume started three browser-test runs at once (T-19b full suite + T-47 parallel `-n auto` with 8 Chromium workers + T-47 two-suites-at-once), which used up the 4 GiB and killed the workspace. The single core is also why every build and test run is slow. Fixed: preflight now reads the real limits; the plan allows only one heavy run at a time on Small; T-47 H-3/H-4 are parked until a bigger tier.
**Recommended:** restart the workspace on **Large (6 cores, 22 GiB)**, or Medium (4 cores, 15 GiB). Same environment; worktrees under /mnt/code/.worktrees survive.

## Wave W2 in progress (see build/plan.md "Combined schedule")
- T-39 Settings page: MERGED (311c986), post-merge 634 pass / 3 skip. Follow-ups queued as T-39f.
- T-19b Agent + Run pages: MERGED 0027b4f (Nick chose to merge; the "40 spans" overlap at 1024 and 5 design follow-ups moved to T-19f).
- T-47 fast e2e: MERGED caa86b6. Browser suite 178 tests in ~2 min (was 18 min); smoke 15 in ~17 s. Parallel runs still need Medium/Large.
- T-48 Settings reachable: MERGED d3ebe91. Settings is in the nav for everyone; in the workspace you are the owner/admin. Live preview (main, real DB): https://se-demo.domino.tech/nick_goble/ai-control-tower/notebookSession/6af513c4b6f82078180d5f25/proxy/9100/ (port 9100; 9888 is the test port).
- Next: W3 = T-20 (navigation + routes for the Superblocks screens), T-19f (table CSS + T-19b r4 design items), follow-ups T-48f and T-50f (optional). SR-6 (publish the App) waiting on Nick: now or after T-20.

Standing rule from Nick: a design agent (visual-reviewer) produces a design brief before and a review after every frontend build.

## Real database (fixed 2026-11-12)
T-50 merged; the real DB is restored on the dataset in DELETE journal mode: 12 agents, integrity ok. Broken files kept as act.db*.broken-20261112030109.

## Waiting on Nick (live and manual steps; not build work)
1. LLM Gateway consent: open https://apps.se-demo.domino.tech/apps/llmgateway/ once and accept, then set `LLM_GATEWAY_URL` and `ACT_LLM_PROVIDER=gateway` (or enter them on the Settings page once T-39 merges).
2. Deploy recon-agent as a Domino Agent and set RECON_AGENT_ID.
3. A service account for recon-agent (ACT_SERVICE_ACCOUNTS) and a second Domino user to approve it.
4. Rerun `python jobs/validate.py` as a Domino Job with the gateway judge.
5. Sign off on the evidence pack and arrange the MRM review.

## If the workspace restarts
Worktrees under .worktrees/ are lost; recreate from origin/task/<id>. Playwright + Node are in the project default environment ai-control-tower-dse-pw.

## 2026-11-12: Provider onboarding (proposed)
Research, design and plan are done: build/provider-onboarding-plan.md. The prototype is at <workspace proxy>/9201/provider-onboarding.html. Waiting on Nick's decisions D-1..D-6 before PA (T-20, PO-1, PO-2) starts.

## 2026-11-12: Provider onboarding shipped + App published
- "Connect a provider" (PO-1..PO-7a) merged: gallery, setup wizard with live detection, access check, scope picker, scans (sample in-process, live as a Domino Job), the review table with suggested tiers + why + overrides, import to Pending review, connection detail, sync schedule, awaiting queue, demo seed for all five clouds.
- Published Domino App "Agent Control Tower": id 6af633efb6f82078180d66d7 (v1 6af633efb6f82078180d66da), URL https://apps.se-demo.domino.tech/u/nick_goble/ai-control-tower/app/?appId=6af633efb6f82078180d66d7, Running, small-k8s, visibility GRANT_BASED (Restricted). Smoke: SPA 200, health ok, 12 agents.
- Notes: the App opens in demo mode because the saved in-app Settings switch is on (toggle in Settings). ACT_PUBLIC_URL is not set (Apps have no app-level env vars; a project var would also hit the workspace). It only matters for live provider return links, not for sample/demo.
- Follow-ups logged in plan.md: PO-3f live-adapter fixes (SigV4, coverage re-crawl, IAM caching), the AWS account/org choice + CloudWatch traces, T-20f nav cue, minor UI items.
- Remaining roadmap: Superblocks tasks T-21..T-33 (Audit, approval gate, findings, Home, policies, roles, ...).
