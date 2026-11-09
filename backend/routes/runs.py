"""Run reconstruction."""
import json

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import db
from ..normalize_span import redact

router = APIRouter()


@router.get("/api/runs/{run_id}/timeline")
def timeline(run_id: str):
    with db.engine.connect() as conn:
        rows = conn.execute(select(db.events.c.event).where(db.events.c.run_id == run_id)
                            .order_by(db.events.c.ts, db.events.c.id)).all()
        run = conn.execute(select(db.runs).where(db.runs.c.id == run_id)).first()
    if not rows:
        raise HTTPException(404, "run not found")
    events = []
    for (ev,) in rows:
        ev = json.loads(ev) if isinstance(ev, str) else ev
        ev["args_redacted"] = redact(ev.get("args_redacted"))
        events.append(ev)
    return {"run_id": run_id, "agent_id": run.agent_id if run else events[0]["agent_id"],
            "started_at": run.started_at if run else None, "ended_at": run.ended_at if run else None,
            "events": events}
