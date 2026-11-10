"""Approvals with builder/approver separation, and the governance view."""
import logging
import threading
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


def _n(v):
    return str(v).strip().casefold() if v is not None else ""


def _mirror_bg(agent_id, ap_id, username):
    try:
        sync = "ok" if governance.mirror_approval(agent_id, username) in ("synced", "skipped") else "failed"
    except Exception as e:  # best-effort: never affect the ACT approval
        log.exception("governance approval mirror failed")
        sync = "failed"
        try:
            with db.engine.begin() as cx:
                _audit(cx, "governance_sync_failed", agent_id, {"error": str(e)[:300]}, actor=(username, username))
        except Exception:
            log.exception("could not audit governance sync failure")
    try:
        with db.engine.begin() as cx:
            cx.execute(db.approvals.update().where(db.approvals.c.id == ap_id).values(governance_sync=sync))
    except Exception:
        log.exception("could not record governance sync result")


@router.post("/api/agents/{agent_id}/approve")
def approve(agent_id: str, body: dict | None = Body(None), user: User = Depends(require_user)):
    run_id = (body or {}).get("run_id") if isinstance(body, dict) else None
    return approve_as(agent_id, user, run_id)


def approve_as(agent_id: str, user: User, run_id=None):
    """Approve with the real separation-of-duties checks. The demo route calls this with the simulated approver."""
    now = datetime.now(timezone.utc)
    actor = (user.user_id, user.username)
    err = None
    with db.engine.begin() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        if a is None:
            raise HTTPException(404, "agent not found")
        if not _n(a.registered_by):
            _audit(cx, "approve_refused", agent_id, {"reason": "registrant unknown"}, actor=actor)
            err = (409, "registrant unknown; cannot verify approver separation")
        elif _n(a.registered_by) == _n(user.user_id) or (
                _n(a.registered_by_username) and _n(a.registered_by_username) == _n(user.username)):
            _audit(cx, "approve_refused", agent_id, {"reason": "approver is the registrant"}, actor=actor)
            err = (403, "the user who registered this agent cannot approve it")
        else:
            days = EXPIRY_DAYS.get(str(a.tier), 365)
            expires = (now + timedelta(days=days)).isoformat()
            upd = cx.execute(db.agents.update().where(
                db.agents.c.id == agent_id, db.agents.c.status.in_(("discovered", "pending_review"))).values(
                status="approved", record={**(a.record or {}), "status": "approved"}))
            if upd.rowcount != 1:
                err = (409, f"agent status {a.status!r} is not approvable")
            else:
                res = cx.execute(db.approvals.insert().values(
                    agent_id=agent_id, approver=user.user_id, decision="approved", created_at=now.isoformat(),
                    expires_at=expires, run_id=run_id, governance_sync="pending"))
                ap_id = res.inserted_primary_key[0]
                _audit(cx, "approved", agent_id, {"expires_at": expires, "tier": a.tier, "run_id": run_id},
                       actor=actor)
    if err:
        raise HTTPException(*err)
    threading.Thread(target=_mirror_bg, args=(agent_id, ap_id, user.username), daemon=True).start()
    return {"id": agent_id, "status": "approved", "approver": user.user_id, "expires_at": expires,
            "run_id": run_id, "governance_sync": "pending"}


def _view(agent_id, a, bundle_id):
    stored = a.governance or {}
    names = list(stored.get("policies") or []) or governance.expected_policy_names()
    return {"bundle_id": bundle_id, "policies": [{"id": None, "name": n} for n in names],
            "stage": stored.get("stage"), "approvals": _approvals(agent_id)}


def _agent_or_404(agent_id):
    with db.engine.connect() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
    if a is None:
        raise HTTPException(404, "agent not found")
    return a


@router.get("/api/agents/{agent_id}/governance")
def get_governance(agent_id: str):
    """Read-only: stored bundle id and expected policies. No Governance API calls."""
    a = _agent_or_404(agent_id)
    return _view(agent_id, a, a.bundle_id or None)


@router.post("/api/agents/{agent_id}/governance")
def post_governance(agent_id: str, user: User = Depends(require_user)):
    _agent_or_404(agent_id)
    try:
        bid = governance.ensure_bundle(agent_id)
    except Exception as e:
        log.exception("ensure_bundle failed")
        raise HTTPException(502, f"governance sync failed: {str(e)[:200]}")
    return _view(agent_id, _agent_or_404(agent_id), bid)
