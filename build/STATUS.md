# STATUS (2026-11-13)

**ACT App is DOWN.** App 6af633efb6f82078180d66d7. Version 2 (commit 5d2090d, origin/main with SET-1) fails at startup. Version 1 was stopped by the new start.

Error: `DatabaseOpenError: Cannot open database /mnt/data/ai-control-tower/act/act.db (nfs4, journal mode DELETE): database is locked` (exit 3, on two attempts).

Beside act.db are a 0-byte `act.db-wal` and a 32 KB `act.db-shm`, both dated Nov 13 16:18. No process in the workspace holds them. A 0-byte WAL holds no uncommitted data.

Proposed fix, waiting for Nick's OK:
1. Copy act.db, act.db-wal and act.db-shm to /mnt/data/ai-control-tower/act/backup-20261113/.
2. Move act.db-wal and act.db-shm aside (rename them to *.stale-20261113).
3. Restart version 2 (POST .../versions/6af743f596e4484d20d87fdb/start).
4. Smoke check: /api/health and /api/settings/access (admin true, open true).

Alternative: scripts/restore_db.py from a known-good copy.

The lasting fix is DB-1 in plan.md (per-pod demo DB, a single writer for act.db).

Done today: SET-1 merged (59e9b45). Plans written: SET-2 (caller identity), SF-0..SF-5 (Snowflake), DB-1 (SQLite over NFS).
