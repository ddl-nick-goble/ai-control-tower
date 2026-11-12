# T-50 re-review r2 @53bc10f: approve with follow-ups
All r1 blocker/majors fixed in code. Follow-ups (T-50f, optional): hot-journal refusal has no override for a stopped app that crashed in DELETE mode (add --force or a message to move -journal aside); BEGIN EXCLUSIVE probe on a WAL dest may checkpoint/remove -wal/-shm before the move-aside (use mode=ro + lock check); post-install integrity failure message should name the .broken-* files; unreadable /proc/mounts falls back to WAL.
Real run order: stop the app, dry run, --apply, confirm PRAGMA journal_mode = delete.
