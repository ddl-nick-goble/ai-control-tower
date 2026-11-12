"""Restore the ACT database from a known-good copy.

Dry run by default; --apply moves the existing DB aside (never deletes) and installs the copy.
Stop the app before using --apply.
    python scripts/restore_db.py --from /path/good.db [--to /path/act.db] [--apply]
"""
import argparse
import fcntl
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import db  # noqa: E402


def counts(conn) -> dict:
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchall()[0][0] for n in names}


def print_counts(label, c) -> None:
    print(label)
    for n, k in c.items():
        print(f"  {n}: {k}")


SIBLINGS = ("", "-wal", "-shm", "-journal")


def _locked(path) -> bool:
    """True if another process holds a POSIX lock on `path` (SQLite's locking primitive)."""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)  # also drops our own lock


def _is_sqlite(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def dest_in_use(dest: Path) -> str:
    """Reason string if something else is using the dest DB, else ''."""
    jr = Path(str(dest) + "-journal")
    if _is_sqlite(dest) and jr.exists() and jr.stat().st_size > 0:
        # A non-empty rollback journal means a write is in flight or a crash left it behind; either way the
        # DB must not be swapped out from under it (and probing it would roll it back).
        return f"{jr} is a non-empty rollback journal (a write is in progress or was interrupted)"
    if _is_sqlite(dest):
        try:
            c = sqlite3.connect(str(dest), timeout=0.5, isolation_level=None)
            try:
                c.execute("BEGIN EXCLUSIVE")
                c.execute("ROLLBACK")
            finally:
                c.close()
        except sqlite3.OperationalError as e:
            if "lock" in str(e).lower() or "busy" in str(e).lower():
                return f"{dest} is locked ({e})"
        except sqlite3.Error:
            pass  # unusable database: that is what we are replacing
    for suffix in SIBLINGS:
        p = str(dest) + suffix
        if os.path.exists(p) and _locked(p):
            return f"{p} is locked by another process"
    return ""


def unique_stamp(dest: Path) -> str:
    base = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1e6) % 1000000:06d}"
    for n in range(1000):
        stamp = f".broken-{base}" + (f"-{n}" if n else "")
        if not any(os.path.lexists(str(dest) + stamp + sfx) for sfx in SIBLINGS):
            return stamp
    raise RuntimeError("could not find an unused .broken-* name")


def prepare_work_copy(src: Path, work: Path, mode: str):
    """Copy src (+ -wal, -journal; never the stale -shm) to a local temp file, verify, fold everything
    into a single file in the dest journal mode. Returns (ok, result, counts)."""
    for suffix in ("", "-wal", "-journal"):
        if Path(str(src) + suffix).is_file():
            shutil.copy2(str(src) + suffix, str(work) + suffix)
    conn = sqlite3.connect(work)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchall()
        ok = result == [("ok",)]
        c = counts(conn) if ok else {}
        if ok:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            conn.execute("PRAGMA journal_mode=DELETE").fetchall()  # fold the WAL in
    finally:
        conn.close()
    if ok and mode == "WAL":
        conn = sqlite3.connect(work)  # fresh connection: set the header to WAL, close checkpoints it
        try:
            conn.execute("PRAGMA journal_mode=WAL").fetchall()
        finally:
            conn.close()
    if ok:
        for suffix in ("-wal", "-shm", "-journal"):
            q = Path(str(work) + suffix)
            if q.exists() and (suffix != "-wal" or q.stat().st_size == 0):
                q.unlink()
    return ok, result, c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dest", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    src = Path(a.src)
    dest = Path(a.dest or db.DB_PATH).expanduser()
    if not src.is_file():
        print(f"error: source {src} not found", file=sys.stderr)
        return 2

    mode = db.journal_mode_for(dest)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "src.db"
        try:
            ok, result, c = prepare_work_copy(src, work, mode)
        except sqlite3.Error as e:
            print(f"error: source {src} failed integrity check: {e}", file=sys.stderr)
            return 1
        if not ok:
            print(f"error: source {src} failed integrity_check: {result[:5]}", file=sys.stderr)
            return 1
        if any(Path(str(work) + sfx).exists() for sfx in ("-wal", "-shm", "-journal")):
            print("error: could not fold the source into a single file", file=sys.stderr)
            return 1

        print(f"source: {src}\ndest:   {dest} (filesystem {db.fs_type(dest)}, journal mode {mode})")
        print_counts("source row counts:", c)
        if not a.apply:
            print("dry run: nothing changed. Re-run with --apply to restore.")
            return 0

        dest.parent.mkdir(parents=True, exist_ok=True)
        busy = dest_in_use(dest)
        if busy:
            print(f"error: {busy}. Stop the ACT app (and anything else using the database), then re-run. "
                  "Nothing was changed.", file=sys.stderr)
            return 3
        stamp = unique_stamp(dest)
        tmp_dest = Path(str(dest) + f".restore-{os.getpid()}.tmp")
        if tmp_dest.exists():
            print(f"error: {tmp_dest} already exists; remove it and re-run. Nothing was changed.", file=sys.stderr)
            return 3
        try:
            # Stage the new file first, so a failed copy leaves the old DB untouched.
            shutil.copyfile(work, tmp_dest)
            with open(tmp_dest, "rb+") as f:
                f.flush()
                os.fsync(f.fileno())
            for suffix in SIBLINGS:
                p = Path(str(dest) + suffix)
                if p.exists():
                    moved = Path(str(dest) + stamp + suffix)
                    if moved.exists():
                        raise RuntimeError(f"{moved} already exists; refusing to overwrite")
                    shutil.move(str(p), str(moved))
                    print(f"moved aside: {p} -> {moved}")
            if dest.exists():
                raise RuntimeError(f"{dest} reappeared; refusing to overwrite")
            os.rename(tmp_dest, dest)
            dfd = os.open(dest.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except (OSError, RuntimeError) as e:
            print(f"error: restore failed: {e}. Old files (if moved) are named {dest}{stamp}*; "
                  f"staged copy: {tmp_dest}", file=sys.stderr)
            return 1

    conn = sqlite3.connect(dest)
    try:
        res = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    if res != [("ok",)]:
        print(f"error: restored {dest} failed integrity_check: {res[:5]}", file=sys.stderr)
        return 1
    eng = db._make_engine(str(dest))
    try:
        db.init_db(eng)
        with eng.connect() as cx:
            print(f"journal mode: {cx.exec_driver_sql('PRAGMA journal_mode').scalar()}")
    finally:
        eng.dispose()
    conn = sqlite3.connect(dest)
    print_counts("restored row counts:", counts(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
