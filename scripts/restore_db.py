"""Restore the ACT database from a known-good copy.

Dry run by default; --apply moves the existing DB aside (never deletes) and installs the copy.
    python scripts/restore_db.py --from /path/good.db [--to /path/act.db] [--apply]
"""
import argparse
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
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def print_counts(label, c) -> None:
    print(label)
    for n, k in c.items():
        print(f"  {n}: {k}")


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

    with tempfile.TemporaryDirectory() as tmp:
        # Work on a private copy (with any -wal/-shm siblings) so the source is never modified.
        work = Path(tmp) / "src.db"
        for suffix in ("", "-wal", "-shm"):
            if Path(str(src) + suffix).is_file():
                shutil.copy2(str(src) + suffix, str(work) + suffix)
        try:
            conn = sqlite3.connect(work)
            result = conn.execute("PRAGMA integrity_check").fetchall()
            ok = result == [("ok",)]
            c = counts(conn) if ok else {}
            if ok:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            conn.close()
        except sqlite3.Error as e:
            print(f"error: source {src} failed integrity check: {e}", file=sys.stderr)
            return 1
        if not ok:
            print(f"error: source {src} failed integrity_check: {result[:5]}", file=sys.stderr)
            return 1

        mode = db.journal_mode_for(dest)
        print(f"source: {src}\ndest:   {dest} (filesystem {db.fs_type(dest)}, journal mode {mode})")
        print_counts("source row counts:", c)
        if not a.apply:
            print("dry run: nothing changed. Re-run with --apply to restore.")
            return 0

        stamp = f".broken-{time.strftime('%Y%m%d-%H%M%S')}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(dest) + suffix)
            if p.exists():
                moved = Path(str(dest) + stamp + suffix)
                shutil.move(str(p), str(moved))
                print(f"moved aside: {p} -> {moved}")
        shutil.copy2(work, dest)

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
