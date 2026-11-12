"""Discovery Job: run one scan (or a scheduled sync) for a connection and write the staged rows to the ACT DB.

Run as a Domino Job:
  python jobs/discover.py --connection <id> --scan <id>     (a scan the App already created)
  python jobs/discover.py --connection <id> --sync          (the scheduled sync: stages, never imports)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connection", required=True)
    ap.add_argument("--scan")
    ap.add_argument("--sync", action="store_true")
    args = ap.parse_args(argv)
    from backend import db, discovery
    db.init_db()
    if args.scan:
        discovery.run_scan(args.scan)
        sid = args.scan
    else:
        sid = discovery.run_sync(args.connection)
    scan = discovery.get_scan(sid)
    print(f"scan {sid}: {scan['state']} {scan['counts']}")
    return 0 if scan["state"] in ("done", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
