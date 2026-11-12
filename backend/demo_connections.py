"""Demo mode only: connected sample tenants for "Connect a provider" (PO-7a).

Seeded states: aws and google connected with a finished scan (New and Changed rows awaiting review), microsoft
connected with an awaiting-import queue (the Fleet banner), databricks needs_attention (one missing permission),
snowflake not connected (the live wizard is walked with "Use sample data"). Scans run in-process on the sample
tenants, so the scans and staged rows are real. Never called outside demo mode; the real DB is never seeded.
"""
import logging

from sqlalchemy import delete, func, insert, select, update

from . import db, discovery
from .connections import sample, store
from .domino_identity import User
from .normalize import now_iso

log = logging.getLogger(__name__)
SEED_USER = User(user_id="demo-seed", username="demo-seed")
# A fleet agent each for the AWS and Google tenants whose stored record lacks its last tool, so the scan finds that
# tool at the source and classes the agent Changed (tool added).
CHANGED_AGENTS = {"aws": "treasury_sweep_agent-Xk3Lq9ZpT2", "google": "122077102645"}


def seed_changed_tool_difference(eng=None) -> list:
    """Demo helper: make the named fleet agents differ from the sample tenant by one tool the tenant has added."""
    done = []
    with (eng or db.engine).begin() as cx:
        for provider, native_id in CHANGED_AGENTS.items():
            row = cx.execute(select(db.agents).where(db.agents.c.native_id == native_id)).first()
            rec = dict(row.record or {}) if row else {}
            man = dict(rec.get("manifest") or {})
            tools = list(man.get("tools") or [])
            if not row or not tools:
                log.warning("demo connections: no tools to drop on %s", native_id)
                continue
            man["tools"] = tools[:-1]
            rec["manifest"] = man
            cx.execute(update(db.agents).where(db.agents.c.id == row.id).values(record=rec))
            done.append(native_id)
    return done


def _connect(provider, state, check=None):
    row, _token = store.create(provider, True, SEED_USER)
    cid, ts = row["id"], now_iso()
    with db.engine.begin() as cx:
        if check is not None:
            cx.execute(insert(db.connection_checks).values(connection_id=cid, ts=ts, state=state, permissions=check))
        cx.execute(update(db.connections).where(db.connections.c.id == cid).values(
            state=state, token_used=True, last_check=ts, updated_at=ts))
    return cid


def clear(eng=None) -> None:
    """Remove every connection, scan, staged row and access check (demo DBs only; callers guarantee that)."""
    with (eng or db.engine).begin() as cx:
        for t in (db.staged_agents, db.scans, db.connection_checks, db.connections):
            cx.execute(delete(t))


def seed() -> dict:
    """Seed the five-cloud demo connections into the active demo DB. Idempotent: does nothing once the seeded
    connections exist. Connections left by earlier demo runs (none of them seeded) are cleared first."""
    with db.engine.connect() as cx:
        if cx.execute(select(func.count()).select_from(db.connections).where(
                db.connections.c.created_by == SEED_USER.user_id)).scalar():
            return {"seeded": False}
    clear()
    seed_changed_tool_difference()
    out = {"seeded": True}
    for provider in ("aws", "google"):  # a finished user-started scan to review
        cid = _connect(provider, "connected", sample.permissions(provider, recheck=True))
        out[provider] = discovery.create_scan(cid, SEED_USER, "scan", True)
        discovery.run_scan(out[provider])
    cid = _connect("microsoft", "connected", sample.permissions("microsoft", recheck=True))
    out["microsoft"] = discovery.run_sync(cid)  # a sync stages new agents as Awaiting import
    perms = sample.permissions("databricks", recheck=False)
    for p in perms:
        if p["name"] == "Read serving endpoints":
            p.update(ok=False, fix="ACT can't list serving endpoints. Grant the ACT service principal CAN_VIEW on "
                                   "the serving endpoints, then check access again.")
    _connect("databricks", "needs_attention", perms)
    return out
