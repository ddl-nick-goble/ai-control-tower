"""Run reconstruction."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select

from .. import db
from ..domino_identity import User, require_user
from ..normalize import _audit, now_iso
from ..normalize_span import redact

router = APIRouter()


def _key(ts):
    """Sortable UTC instant for mixed 'Z' and '+00:00' timestamps."""
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return float("inf")


@router.post("/api/runs/{run_id}/override")
def override(run_id: str, body: dict = Body(...), user: User = Depends(require_user)):
    reason = (body or {}).get("reason")
    if not reason or not isinstance(reason, str):
        raise HTTPException(422, "body must be {\"reason\": \"...\"}")
    ts = now_iso()
    with db.engine.begin() as cx:
        ev = cx.execute(select(db.events.c.agent_id).where(db.events.c.run_id == run_id).limit(1)).first()
        if ev is None:
            raise HTTPException(404, "run not found")
        cx.execute(db.overrides.insert().values(run_id=run_id, agent_id=ev[0], actor_user_id=user.user_id,
                                                actor_username=user.username, reason=reason, ts=ts))
        _audit(cx, "override", run_id, {"reason": reason, "agent_id": ev[0]}, actor=(user.user_id, user.username))
    return {"run_id": run_id, "kind": "override", "ts": ts, "reason": reason}


@router.get("/api/runs/{run_id}/timeline")
def timeline(run_id: str):
    with db.engine.connect() as conn:
        rows = conn.execute(select(db.events.c.event).where(db.events.c.run_id == run_id)
                            .order_by(db.events.c.ts, db.events.c.id)).all()
        run = conn.execute(select(db.runs).where(db.runs.c.id == run_id)).first()
        ovs = conn.execute(select(db.overrides).where(db.overrides.c.run_id == run_id)).all()
        aps = conn.execute(select(db.approvals).where(db.approvals.c.run_id == run_id)).all()
    if not rows:
        raise HTTPException(404, "run not found")
    events = []
    for (ev,) in rows:
        ev = json.loads(ev) if isinstance(ev, str) else ev
        ev["args_redacted"] = redact(ev.get("args_redacted"))
        events.append(ev)
    agent_id = run.agent_id if run else events[0]["agent_id"]
    for o in ovs:
        events.append({"run_id": run_id, "agent_id": o.agent_id or agent_id, "ts": o.ts, "kind": "override",
                       "name": "override", "actor": o.actor_user_id, "reason": o.reason})
    for a in aps:
        events.append({"run_id": run_id, "agent_id": a.agent_id, "ts": a.created_at, "kind": "approval",
                       "name": "approval", "actor": a.approver, "decision": a.decision,
                       "expires_at": a.expires_at})
    events.sort(key=lambda e: _key(e.get("ts")))
    return {"run_id": run_id, "agent_id": agent_id,
            "started_at": run.started_at if run else None, "ended_at": run.ended_at if run else None,
            "events": events}
