# STATUS (2026-11-13)

ACT App 6af633efb6f82078180d66d7 is **Running** again: version 2, commit 5d2090d (main with SET-1).
Smoke checks:
- /api/health: 200
- SPA root: 200
- /api/settings/access: 200, admin true, open true
- /api/agents: 12 agents

## How it was fixed
act.db and demo.db were stored in WAL mode, which can't work across pods on NFS. The startup switch to DELETE mode needed a lock it could not get.

Fix: both files were replaced with DELETE-mode copies, converted on local disk from the backups. Integrity was ok and row counts were identical (act.db: 12 agents). Nothing was deleted.
- Old files: act.db.walmode-20261113, demo.db.walmode-20261113, plus *-wal/*-shm .stale-20261113 and .stale2-20261113.
- Backups: /mnt/data/ai-control-tower/act/backup-20261113/.

## Stopped: DB-1 needs a decision (3 builder rounds used)
DB-1 (task/DB-1 at b316a5b) is the lasting fix: per-pod demo DB, bounded lock wait, WAL->DELETE conversion, health reports starting/error, background open with retry, no auto-rename.
- Code suite after test fixes: 1024 pass / 3 skip on a clean run, but tests/test_t4_intake.py::test_ac1_intake_lists_all_five_sources fails about 4 in 17 runs on DB-1 (fleet 13, expected 12). It passed 3 of 3 runs on main.
- Cause not confirmed. Likely: the background DB open/retry thread from an earlier test's app outlives that app and writes into the next test's DB. If so, it is an app bug in DB-1 (the thread is not tied to app shutdown).
- Not yet run on round 3: the e2e suite and code review.
- Rule: 3 rounds per task, so the builder is not re-sent without the user's OK.
- The live App is unaffected (main, DELETE-mode DBs, Running).

## Planned
SET-2 (caller identity), SF-0..SF-5 (Snowflake), DB-1.
