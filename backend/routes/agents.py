"""Fleet inventory, agent record, tier scoring, and intake upsert."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select

from .. import db
from ..domino_identity import User, require_user
from ..normalize import _audit, apply_record
from ..tiering import TieringInputRejected, score_tier

router = APIRouter()


def _tier(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@router.get("/api/agents")
def list_agents(source: str | None = None, tier: int | None = None, include_shadow: bool = False):
    q = select(db.agents)
    if source:
        q = q.where(db.agents.c.source_platform == source)
    if tier is not None:
        q = q.where(db.agents.c.tier == str(tier))
    if not include_shadow:
        q = q.where(db.agents.c.status != "shadow")
    with db.engine.connect() as cx:
        rows = cx.execute(q.order_by(db.agents.c.source_platform, db.agents.c.name)).all()
        open_f = dict(cx.execute(select(db.findings.c.agent_id, func.count()).where(db.findings.c.status == "open")
                                 .group_by(db.findings.c.agent_id)).all())
    return [{"id": r.id, "source_platform": r.source_platform, "native_id": r.native_id, "name": r.name,
             "owner": r.owner, "tier": _tier(r.tier), "status": r.status, "last_seen": r.last_seen,
             "open_findings": int(open_f.get(r.id, 0))} for r in rows]


@router.get("/api/agents/{agent_id}")
def get_agent(agent_id: str):
    with db.engine.connect() as cx:
        r = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
    if r is None or not r.record:
        raise HTTPException(404, "agent not found")
    rec = dict(r.record)
    rec.update(id=r.id, name=r.name, owner=r.owner or rec.get("owner"), status=r.status, last_seen=r.last_seen,
               tier=_tier(r.tier), tier_reasoning=r.tier_reasoning, criteria=r.criteria or rec.get("criteria"))
    return rec


@router.post("/api/agents/{agent_id}/tier")
def set_tier(agent_id: str, body: dict = Body(...), user: User = Depends(require_user)):
    criteria = body.get("criteria") if isinstance(body, dict) else None
    if not isinstance(criteria, dict):
        raise HTTPException(422, "body must be {\"criteria\": {...}}")
    try:
        res = score_tier(criteria)
    except (TieringInputRejected, ValueError) as e:
        raise HTTPException(422, str(e))
    with db.engine.begin() as cx:
        r = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        if r is None:
            raise HTTPException(404, "agent not found")
        rec = dict(r.record or {})
        rec.update(tier=res.tier, tier_reasoning=res.reasoning, criteria=criteria)
        cx.execute(db.agents.update().where(db.agents.c.id == agent_id).values(
            tier=str(res.tier), tier_reasoning=res.reasoning, criteria=criteria, record=rec))
        _audit(cx, "retier" if str(res.tier) != str(r.tier) else "tier_score", agent_id,
               {"from": r.tier, "to": res.tier, "score": res.score, "reasoning": res.reasoning, "manual": True},
               actor=(user.user_id, user.username))
    return {"id": agent_id, "tier": res.tier, "score": res.score, "reasoning": res.reasoning}


@router.post("/api/agents/intake")
def post_intake(body: dict = Body(...), user: User = Depends(require_user)):
    """Upsert normalized agent records produced by jobs/intake.py when it runs outside this process."""
    counts = {"created": 0, "updated": 0, "retiered": 0}
    with db.engine.begin() as cx:
        for rec in body.get("records", []):
            counts[apply_record(cx, rec)] += 1
    return counts
