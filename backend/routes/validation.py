"""Validation record API (AC-10): store and read the repeated-evaluation score distribution."""
import logging
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select

from .. import db, governance
from ..domino_identity import User, require_user
from ..normalize import _audit

log = logging.getLogger(__name__)
router = APIRouter()
MIN_PROMPTS, MIN_REPS = 20, 5
NUMERIC = ("mean", "p5", "p95", "pass_rate")


MAX_SCORES = 10_000


def _num(v, name, lo=0.0, hi=1.0):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise HTTPException(422, f"{name} must be a finite number between {lo:g} and {hi:g}")
    return float(v)


def _count(body, k, required=False):
    v = body.get(k)
    if v is None and not required:
        return 0
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise HTTPException(422, f"{k} must be a non-negative integer")
    return v


def _str(v, name, required=False):
    if v is None and not required:
        return None
    if not isinstance(v, str) or len(v) > 300 or (required and not v):
        raise HTTPException(422, f"{name} must be a string")
    return v


def _view(r):
    return {"id": r.id, "agent_id": r.agent_id, "kind": r.kind, "result": r.result,
            "created_at": r.created_at, "detail": r.detail}


@router.post("/api/agents/{agent_id}/validation")
def post_validation(agent_id: str, body: dict = Body(...), user: User = Depends(require_user)):
    n = {k: _num(body.get(k), k) for k in NUMERIC}
    n_prompts, reps = _count(body, "n_prompts", True), _count(body, "reps", True)
    if n_prompts < MIN_PROMPTS or reps < MIN_REPS:
        raise HTTPException(422, f"validation needs at least {MIN_PROMPTS} prompts and {MIN_REPS} repetitions")
    if n_prompts > 1000 or reps > 50:
        raise HTTPException(422, "n_prompts must be at most 1000 and reps at most 50")
    if not n["p5"] <= n["mean"] <= n["p95"]:
        raise HTTPException(422, "distribution must satisfy p5 <= mean <= p95")
    if not isinstance(body.get("judge_alias"), str) or not body["judge_alias"]:
        raise HTTPException(422, "judge_alias is required")
    threshold = _num(body["threshold"], "threshold") if body.get("threshold") is not None else 0.7
    detail = {**n, "threshold": threshold, "n_prompts": n_prompts, "reps": reps,
              "judge_alias": _str(body["judge_alias"], "judge_alias", True),
              "judge_model_id": _str(body.get("judge_model_id"), "judge_model_id"),
              "error_count": _count(body, "error_count"), "invalid_count": _count(body, "invalid_count"),
              "experiment": _str(body.get("experiment"), "experiment")}
    detail["judge_version"] = _str(body.get("judge_version"), "judge_version") or detail["judge_model_id"]
    if body.get("scores") is not None:
        sc = body["scores"]
        if not isinstance(sc, list) or len(sc) > MAX_SCORES:
            raise HTTPException(422, f"scores must be a list of at most {MAX_SCORES} numbers")
        detail["scores"] = [_num(x, "scores[]") for x in sc]
        if len(sc) != n_prompts * reps:
            raise HTTPException(422, "scores must contain n_prompts * reps values")
        if abs(n["mean"] - sum(detail["scores"]) / len(sc)) > 1e-6:
            raise HTTPException(422, "mean does not match the mean of scores")
    if body.get("mlflow_run_ids") is not None:
        ids = body["mlflow_run_ids"]
        if not isinstance(ids, list) or len(ids) > 1000 or not all(isinstance(x, str) and len(x) <= 200 for x in ids):
            raise HTTPException(422, "mlflow_run_ids must be a list of strings")
        detail["mlflow_run_ids"] = ids
    body = {**body, **n}
    result = "pass" if n["mean"] >= threshold else "fail"
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
                   f"{body['pass_rate']:.1%}, result {result}; judged by a frontier model via the LLM gateway.")
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
