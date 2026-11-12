# E2E speed plan (APPROVED by Nick 2026-11-12)

Goal: cut a task's UI cycle on the Small tier (1 core, 4 GiB) from ~25 min per run to ~5 min, without weakening any assertion.

## Where the time goes today (measured from the repo, 2026-11-12)
1. **Every run is the full suite.** 217 browser tests run serially before each merge, and again at wave end, even when a task touched one page.
2. **Capture tests run as tests.** 54 screenshot-only tests (test_t19a_captures, test_t19b_run2/run3/run4_captures) run in every full suite. run2 and run3 are historical: they re-shoot T-19b review rounds that are already merged.
3. **Full-page screenshots after nearly every test.** The `shot` fixture waits 250 ms and takes a full-page PNG. On one core, PNG encoding of 2,000+ px pages is a large share of each test.
4. **Eight app starts per full run.** conftest starts one app, and 7 test files (t12, t15, t18, t19a_preview_p2, t19a_shell_fleet, t39, T-48's new file) each start their own app with its own seed. Each start is seed + uvicorn boot + health wait.
5. **Fixed sleeps.** About 49 `wait_for_timeout` / `time.sleep` calls across the e2e files, instead of waiting for the condition.
6. **Per-worktree npm install.** Every new worktree gets its own node_modules (npm install on 1 core takes minutes). The fresh checkout also makes sources newer than dist, so app.sh rebuilds the frontend on first start.
7. **Old browser and app processes.** Before this session, runs overlapped and crashed the workspace, which cost whole sessions. That's fixed by the one-browser rule.

## Plan

### A. Tiered runs (orchestration only, takes effect immediately, no code)
| When | What runs | Target time on Small |
|---|---|---|
| Test-first (red check) | the task's own e2e file only | 1-3 min |
| After each builder round | task file + `smoke` set (about 15 tests: shell, nav, Fleet, Agent, Run, Settings load) | 3-5 min |
| Pre-merge (once per task) | full suite, `-m "not capture"` | ~12-15 min after B-D |
| Wave end | full suite on main, **skipped** when main's tree equals the last pre-merge-tested tree (merge with no conflicts, nothing else merged since) | 0 or ~12 min |
| Design review | the capture CLI only for the screens the reviewer needs (max 8), not tests | 1-2 min |
Also: code review and design review keep running in parallel with the UI run (they don't use the browser).

### B. Captures out of the test suite (test-author, tests/e2e only)
- Add `@pytest.mark.capture` to all capture tests, and `addopts = -m "not capture"` in pytest.ini, so they never run in regression.
- Delete test_t19b_run2_captures.py and test_t19b_run3_captures.py (superseded; their PNGs stay in the QA folder).
- Bring T-47's H-5 capture CLI (tests/e2e/capture.py, already written on task/T-47: `--delay-api`/`--fail-api` for loading and error states) to main, so design captures are one command per screen.

### C. Cheaper screenshots (test-author)
- `shot` takes a viewport screenshot by default; `full_page=True` only when the test asks for it. Drop the fixed 250 ms wait (wait for network idle instead).
- Regression runs use `--screenshot only-on-failure` and skip the per-test "-then" PNGs. Captures for reviewers come from B.

### D. One app per run (test-author, tests/e2e/conftest.py)
- The 7 per-file app starts get folded into the session app where they only differ in seed data or settings: reset state through the API or a DB snapshot copy between files instead of restarting.
- Files that truly need different env (owner mode on/off, demo lock) share one extra app per distinct env, cached by an env key, started once per run.
- Seed once, then copy the seeded SQLite file per app instead of re-running seed.py.

### E. Replace sleeps with waits (test-author)
- Every `wait_for_timeout`/`time.sleep` becomes `expect(...).to_be_visible()` / `wait_for_response` / polling on the condition. Keep a lint check (grep in preflight or a conftest guard) so new sleeps don't creep back.

### F. Worktree setup (coordinator, no app code)
- New worktrees symlink `node_modules` to a shared copy (/mnt/code/node_modules, installed once), and touch frontend/dist after checkout so app.sh doesn't rebuild an unchanged frontend. Builders rebuild dist when they change frontend/src (they already commit dist).

### G. Hardware (Nick's call, biggest single win)
- On Medium (4 cores) or Large (6 cores), T-47's xdist (`-n 3` / `-n 5`) on top of A-F makes the pre-merge full run ~3-5 min. Nothing here depends on it.

## Measure first, then after
- Step 0: one serial full run on main with `--durations=40` to record the baseline (per-test and per-file times, app start time). Then the same after B-E. Results go in this file.

## How it's delivered
- A takes effect now in my orchestration (no code change).
- F is coordinator setup.
- B-E become one task, **T-47 (reworked)**: "fast serial e2e on Small". It touches tests/e2e/, pytest.ini, and the README test section only. Owner: test-author. QA: code-reviewer, plus a before/after timing comparison with an equal pass count (no assertion removed except the deleted superseded capture files). T-47's existing worktree has H-1/H-2/H-5 already; H-3/H-4 stay parked until Medium/Large.
- Scheduling: T-47 touches tests/e2e/conftest.py and test files that T-48 also edits (test_t39_settings.py). So it starts as soon as T-48 merges, before T-20. It is about 1-2 hours of agent time on Small, and pays back from the second task after it.

## Step 0 baseline (2026-11-12, main after T-19b merge, serial, Small, builder running alongside)
217 passed in 1086.6 s (18:06); peak RSS 467 MB. Setup 670.8 s (62%), call 408.6 s, teardown 5.9 s.
- Capture files: 496.6 s (46%): t19b run2 135.4, run3 140.2, run4 143.6, t19a captures 77.4. run2/run3/run4 are near-identical.
- Per-file app start: 35-38 s each, paid once per file that starts its own app.
- Fixed waits: test_cap_agent_loading 36.3 s (x3 files), test_t15 test_d10_off_by_default 35.8 s, cap_run_loading ~6.5 s (x3).
- Other files: t39 126.2, t19b pages 111.9, t19a shell 96.7, t15 83.9, t18 56.7, t19a p2 36.2, t10 31.4, t9 18.4, t12 17.8, t13 8.5, t14 0.9.
Expected after B-E: drop ~500 s of captures, ~6 x 36 s of app starts, ~150 s of fixed waits -> about 5-6 min serial on Small. Raw output: qa/e2e-baseline/{run.txt,full.txt}.
