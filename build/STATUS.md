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

## In progress
DB-1 is the lasting fix: a per-pod demo DB, a lock wait at startup, self-heal, WAL conversion, and e2e isolation. Tests are being written.

## Planned
SET-2 (caller identity), SF-0..SF-5 (Snowflake), DB-1.
