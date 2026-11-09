"""Intake job: run all five adapters, upsert the inventory, post fixture runs to trace ingest.

Run as a Domino job (`python jobs/intake.py`). With ACT_URL set it calls the ACT backend over HTTP
(bearer token from the in-run token endpoint); otherwise it writes to the DB in-process.
Set ACT_INTAKE_INPROCESS=1 to force in-process.
"""
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("intake")
# weekend-drift.json is the AC-12 scenario (a Saturday transfer over limit): posted by that scenario, not by intake.
SKIP_RUNS = {"weekend-drift.json"}


def _adapters():
    from adapters.aws import AwsAdapter
    from adapters.databricks import DatabricksAdapter
    from adapters.domino import DominoAdapter
    from adapters.google import GoogleAdapter
    from adapters.microsoft import MicrosoftAdapter
    return [AwsAdapter(), GoogleAdapter(), MicrosoftAdapter(), DatabricksAdapter(), DominoAdapter()]


def _run_files():
    from adapters.base import fixtures_dir
    d = fixtures_dir() / "runs"
    skip = SKIP_RUNS | set(filter(None, os.environ.get("ACT_INTAKE_SKIP_RUNS", "").split(",")))
    return sorted(p for p in d.glob("*.json") if p.name not in skip) if d.is_dir() else []


def _http():
    import requests
    base = os.environ["ACT_URL"].rstrip("/")
    token = requests.get("http://localhost:8899/access-token", timeout=5).text.strip()
    return requests, base, {"Authorization": f"Bearer {token}"}


def run_intake() -> dict:
    from backend.normalize import normalize
    records, errors = [], []
    for ad in _adapters():
        try:
            records += [normalize(sa) for sa in ad.list_agents()]
        except Exception as e:
            log.exception("adapter %s failed", ad.platform)
            errors.append(f"{ad.platform}: {e}")
    inproc = os.environ.get("ACT_INTAKE_INPROCESS") == "1" or not os.environ.get("ACT_URL")
    runs_posted = 0
    if inproc:
        from backend import db
        from backend.normalize import apply_record
        db.init_db()
        counts = {"created": 0, "updated": 0, "retiered": 0}
        with db.engine.begin() as cx:
            for rec in records:
                counts[apply_record(cx, rec)] += 1
        from backend.otlp import parse_request
        from backend.routes.traces import ingest
        for p in _run_files():
            try:
                ingest(parse_request(p.read_bytes(), "application/json"))
                runs_posted += 1
            except Exception as e:
                log.exception("run file %s failed", p.name)
                errors.append(f"{p.name}: {e}")
    else:
        requests, base, headers = _http()
        r = requests.post(f"{base}/api/agents/intake", json={"records": records}, headers=headers, timeout=60)
        r.raise_for_status()
        counts = r.json()
        for p in _run_files():
            rr = requests.post(f"{base}/v1/traces", data=p.read_bytes(), timeout=60,
                               headers={**headers, "Content-Type": "application/json"})
            if rr.status_code < 300:
                runs_posted += 1
            else:
                errors.append(f"{p.name}: HTTP {rr.status_code}")
    return {"agents": len(records), **counts, "runs_posted": runs_posted, "errors": errors}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    out = run_intake()
    print(json.dumps(out, indent=2))
    sys.exit(1 if out["errors"] and not out["agents"] else 0)
