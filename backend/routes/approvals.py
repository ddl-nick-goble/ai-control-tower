"""Approvals with builder/approver separation, and the governance view."""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select

from .. import db, governance
from ..domino_identity import User, require_user
from ..normalize import _audit

router = APIRouter()
log = logging.getLogger(__name__)
EXPIRY_DAYS = {"1": 90, "2": 180, "3": 365, "4": 365}


def _approvals(agent_id):
    with db.engine.connect() as cx:
        rows = cx.execute(select(db.approvals).where(db.approvals.c.agent_id == agent_id)
                          .order_by(db.approvals.c.id)).all()
    return [{"id": r.id, "approver": r.approver, "decision": r.decision, "created_at": r.created_at,
             "expires_at": r.expires_at, "run_id": r.run_id, "governance_sync": r.governance_sync} for r in rows]


@router.post("/api/agents/{agent_id}/approve")
def approve(agent_id: str, body: dict | None = Body(None), user: User = Depends(require_user)):
    run_id = (body or {}).get("run_id") if isinstance(body, dict) else None
    now = datetime.now(timezone.utc)
    with db.engine.begin() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        if a is None:
            raise HTTPException(404, "agent not found")
        if a.registered_by and a.registered_by == user.user_id:
            _audit(cx, "approve_refused", agent_id,
                   {"reason": "approver is the registrant"}, actor=(user.user_id, user.username))
            refused = True
        else:
            refused = False
            days = EXPIRY_DAYS.get(str(a.tier), 365)
            expires = (now + timedelta(days=days)).isoformat()
            res = cx.execute(db.approvals.insert().values(
                agent_id=agent_id, approver=user.user_id, decision="approved", created_at=now.isoformat(),
                expires_at=expires, run_id=run_id))
            ap_id = res.inserted_primary_key[0]
            cx.execute(db.agents.update().where(db.agents.c.id == agent_id).values(
                status="approved", record={**(a.record or {}), "status": "approved"}))
            _audit(cx, "approved", agent_id, {"expires_at": expires, "tier": a.tier, "run_id": run_id},
                   actor=(user.user_id, user.username))
    if refused:
        raise HTTPException(403, "the user who registered this agent cannot approve it")
    try:
        sync = governance.mirror_approval(agent_id, user.username)
    except Exception as e:  # best-effort: never fail the ACT approval
        log.exception("governance approval mirror failed")
        sync = "failed"
        with db.engine.begin() as cx:
            _audit(cx, "governance_sync_failed", agent_id, {"error": str(e)[:300]},
                   actor=(user.user_id, user.username))
    with db.engine.begin() as cx:
        cx.execute(db.approvals.update().where(db.approvals.c.id == ap_id).values(governance_sync=sync))
    return {"id": agent_id, "status": "approved", "approver": user.user_id, "expires_at": expires,
            "run_id": run_id, "governance_sync": sync}


@router.get("/api/agents/{agent_id}/governance")
def get_governance(agent_id: str):
    with db.engine.connect() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
    if a is None:
        raise HTTPException(404, "agent not found")
    stored = a.governance or {}
    bundle_id, names, stage, error = a.bundle_id, list(stored.get("policies") or []), stored.get("stage"), None
    try:
        bundle_id = governance.ensure_bundle(agent_id)
        b = governance.get_bundle(bundle_id)
        live = [governance._pname(p) for p in b.get("policies") or []]
        if live:
            names = live
            stage = b.get("stage") or stage
            with db.engine.begin() as cx:
                cx.execute(db.agents.update().where(db.agents.c.id == agent_id).values(
                    governance={"policies": names, "stage": stage}))
    except Exception as e:
        log.exception("governance read failed")
        error = str(e)[:300]
    return {"bundle_id": bundle_id, "policies": [{"id": None, "name": n} for n in names], "stage": stage,
            "approvals": _approvals(agent_id), "error": error}
