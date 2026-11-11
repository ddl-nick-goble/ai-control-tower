"""Demo-mode routes (/api/demo/*). Mounted by backend/main.py only when ACT_DEMO_MODE is on.

The simulated approver goes through the same approval code as a real approver, so builder/approver
separation stays real; it is only the identity that is supplied by the app instead of a second Domino user.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from .. import db, demo
from ..domino_identity import User, require_user
from .approvals import approve_as



def _demo_only():
    if not demo.enabled():  # /api/demo/* exists only while demo mode is on (env or the Settings switch)
        raise HTTPException(404, "Not found")


router = APIRouter(dependencies=[Depends(_demo_only)])
log = logging.getLogger(__name__)


def _agent(agent_id, eng=None):
    with (eng or db.engine).connect() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
    if a is None:
        raise HTTPException(404, "agent not found")
    return a


@router.get("/api/demo/state")
def state():
    return {"demo_mode": True, "stand_ins": demo.stand_ins(), "banner": demo.BANNER_TEXT}


@router.post("/api/demo/approve/{agent_id}")
def demo_approve(agent_id: str, user: User = Depends(require_user)):
    try:
        with demo.active_demo_engine() as eng:  # held so a switch-off cannot land between the check and the write
            _agent(agent_id, eng)
            r = _approve(agent_id, user)
    except demo.DemoGuardError as e:
        raise HTTPException(409, str(e))
    return r


def _approve(agent_id, user):
    r = approve_as(agent_id, User(user_id=demo.APPROVER_ID, username=demo.APPROVER_NAME),
                   audit_extra={"simulated": True, "requested_by": user.user_id, "requested_by_username": user.username})
    return {**r, "simulated": True, "note": "approved by the simulated demo approver, not a second Domino user"}


@router.post("/api/demo/run/{agent_id}")
def demo_run(agent_id: str, _user: User = Depends(require_user)):
    from jobs import seed_demo
    try:
        with demo.active_demo_engine() as eng:  # held for the whole run: the engine cannot change under it
            a = _agent(agent_id, eng)
            if a.name != "recon-agent":
                raise HTTPException(409, "only recon-agent can be run in demo mode")
            run_id = seed_demo.run_recon_once(agent_id, eng)
    except demo.DemoGuardError as e:
        raise HTTPException(409, str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("demo run failed")
        raise HTTPException(500, f"demo run failed: {type(e).__name__}")
    return {"run_id": run_id, "agent_id": agent_id, "simulated": True}


@router.post("/api/demo/reset")
def demo_reset(_user: User = Depends(require_user)):
    try:
        summary = demo.reset()
    except demo.DemoGuardError as e:
        raise HTTPException(409, str(e))
    return {"status": "reset", "agents": summary.get("agents")}
