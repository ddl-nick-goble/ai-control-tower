"""Findings API and registration of the drift detector on trace ingest."""
from fastapi import APIRouter, HTTPException
from sqlalchemy import case, select

from .. import db, drift
from . import traces

router = APIRouter()
traces.register_run_hook(drift.detect_run)


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except ValueError:
        return None


def _out(r) -> dict:
    d = dict(r.detail or {})
    o = {"id": str(r.id), "type": r.type or r.rule, "severity": r.severity, "agent_id": r.agent_id,
         "run_id": r.run_id, "span_id": r.span_id, "tool": r.tool, "ts": r.ts, "status": r.status,
         "reason": r.reason, "observed_value": _num(r.observed_value), "limit": _num(r.limit),
         "explanation": d.get("explanation") or r.message, "created_at": r.created_at, "detail": d}
    return {k: v for k, v in o.items() if v is not None}


@router.get("/api/agents/{agent_id}/findings")
def agent_findings(agent_id: str):
    with db.engine.connect() as cx:
        rows = cx.execute(select(db.findings).where(db.findings.c.agent_id == agent_id).order_by(
            case((db.findings.c.status == "open", 0), else_=1), db.findings.c.id.desc())).all()
    return [_out(r) for r in rows]


@router.get("/api/findings/{finding_id}")
def get_finding(finding_id: int):
    with db.engine.connect() as cx:
        r = cx.execute(select(db.findings).where(db.findings.c.id == finding_id)).first()
    if r is None:
        raise HTTPException(404, "finding not found")
    o = _out(r)
    if r.run_id:
        o["timeline_url"] = f"runs/{r.run_id}/timeline"
    return o
