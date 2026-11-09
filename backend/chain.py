"""Append-only hash chain over the events table."""
import hashlib
import json
import threading

from sqlalchemy import insert, select

from . import db

GENESIS = "0" * 64
_lock = threading.Lock()


def canonical_json(event) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def compute_hash(prev_hash: str, event) -> str:
    return hashlib.sha256((prev_hash + canonical_json(event)).encode()).hexdigest()


def begin_write(conn) -> None:
    """Take the SQLite write lock (BEGIN IMMEDIATE) so the prev_hash read and the insert
    happen in one write transaction, safe across processes. Must be the first statement
    on a fresh connection; the caller's commit/rollback ends it."""
    conn.exec_driver_sql("BEGIN IMMEDIATE")


def append_events(conn, items: list[dict]) -> None:
    """items: dicts with run_id, agent_id, ts, kind, raw_span, event. Caller holds a write
    txn on conn (see begin_write) and `with chain.lock:` around the whole transaction."""
    prev = conn.execute(select(db.events.c.hash).order_by(db.events.c.id.desc()).limit(1)).scalar() or GENESIS
    rows = []
    for it in items:
        h = compute_hash(prev, it["event"])
        rows.append({**it, "prev_hash": prev, "hash": h})
        prev = h
    if rows:
        conn.execute(insert(db.events), rows)


lock = _lock


def verify() -> dict:
    prev = GENESIS
    n = 0
    ok = True
    with db.engine.connect() as conn:
        for r in conn.execute(select(db.events.c.event, db.events.c.prev_hash, db.events.c.hash)
                              .order_by(db.events.c.id)):
            ev = r.event
            if isinstance(ev, str):
                ev = json.loads(ev)
            if r.prev_hash != prev or compute_hash(prev, ev) != r.hash:
                ok = False
                break
            prev = r.hash
            n += 1
    return {"ok": ok, "events": n}
