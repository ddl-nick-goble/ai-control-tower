"""Validation record API (AC-10): store and read the repeated-evaluation score distribution."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import db, evidence, governance
from ..domino_identity import User, require_user
from ..normalize import _audit

log = logging.getLogger(__name__)
router = APIRouter()
MIN_PROMPTS, MIN_REPS = 20, 5
NUMERIC = ("mean", "p5", "p95", "pass_rate")


def _num(body, k):
    v = body.get(k)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise HTTPException(422, f"{k} must be a number")
    return float(v)


def _view(r):
    return {"id": r.id, "agent_id": r.agent_id, "kind": r.kind, "result": r.result,
            "created_at": r.created_at, "detail": r.detail}


@router.post("/api/agents/{agent_id}/validation")
def post_validation(agent_id: str, body: dict, user: User = Depends(require_user)):
    for k in NUMERIC:
        _num(body, k)
    for k in ("n_prompts", "reps"):
        if not isinstance(body.get(k), int) or isinstance(body.get(k), bool):
            raise HTTPException(422, f"{k} must be an integer")
    if body["n_prompts"] < MIN_PROMPTS or body["reps"] < MIN_REPS:
        raise HTTPException(422, f"validation needs at least {MIN_PROMPTS} prompts and {MIN_REPS} repetitions")
    if not body.get("judge_alias"):
        raise HTTPException(422, "judge_alias is required")
    threshold = float(body["threshold"]) if isinstance(body.get("threshold"), (int, float)) else 0.7
    result = "pass" if body["mean"] >= threshold else "fail"
    detail = {**body, "threshold": threshold}
    with db.engine.connect() as cx:
        agent = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
    if agent is None:
        raise HTTPException(404, "agent not found")
    with db.engine.begin() as cx:
        rid = cx.execute(db.validation_records.insert().values(
            agent_id=agent_id, kind="repeated_eval", result=result,
            created_at=datetime.now(timezone.utc).isoformat(), detail=detail)).inserted_primary_key[0]
        _audit(cx, "validation_recorded", agent_id, {"validation_id": rid, "result": result, "mean": body["mean"]},
               actor=(user.user_id, user.username))
    governance_note = None
    bundle_id = agent.bundle_id or None
    if bundle_id:
        summary = (f"ACT validation record {rid}: {body['n_prompts']} prompts x {body['reps']} repetitions, "
                   f"mean {body['mean']:.3f}, p5 {body['p5']:.3f}, p95 {body['p95']:.3f}, pass rate "
                   f"{body['pass_rate']:.1%}, result {result}; judge alias {evidence.clean(body['judge_alias'])}.")
        try:
            governance.attach_evidence(bundle_id, "validation", {
                "summary": summary, "citation": f"validation_records:{rid}"})
            governance_note, action = "attached", "validation_attached"
        except Exception as e:  # best effort: the record is already stored
            log.exception("validation evidence submit failed")
            governance_note, action = "attach_failed", "validation_attach_failed"
        with db.engine.begin() as cx:
            _audit(cx, action, agent_id, {"validation_id": rid, "bundle_id": bundle_id}, actor=(user.user_id, user.username))
    return {"id": rid, "agent_id": agent_id, "kind": "repeated_eval", "result": result, "governance": governance_note}


@router.get("/api/agents/{agent_id}/validation")
def get_validation(agent_id: str):
    with db.engine.connect() as cx:
        if cx.execute(select(db.agents.c.id).where(db.agents.c.id == agent_id)).first() is None:
            raise HTTPException(404, "agent not found")
        r = cx.execute(select(db.validation_records).where(db.validation_records.c.agent_id == agent_id)
                       .order_by(db.validation_records.c.id.desc())).first()
    if r is None:
        raise HTTPException(404, "no validation record")
    return _view(r)
