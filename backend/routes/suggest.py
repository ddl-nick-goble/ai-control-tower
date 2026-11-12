"""POST /api/suggest/scores and /api/suggest/override (PO-2): suggested criteria scores and audited overrides."""
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..domino_identity import User, require_user
from ..normalize import _audit
from ..suggest import suggest_scores
from ..tiering import load_config, score_tier

router = APIRouter()


def _result(suggestions):
    r = score_tier({k: v["score"] for k, v in suggestions.items()})
    return {"suggestions": suggestions, "tier": r.tier, "weighted": r.score}


def _agent(body):
    if not isinstance(body, dict) or not isinstance(body.get("agent"), dict):
        raise HTTPException(422, "agent must be an object")
    return body["agent"]


@router.post("/api/suggest/scores")
def post_scores(body: dict = Body(...), user: User = Depends(require_user)):
    return _result(suggest_scores(_agent(body)))


@router.post("/api/suggest/override")
def post_override(body: dict = Body(...), user: User = Depends(require_user)):
    agent = _agent(body)
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise HTTPException(422, "An override needs a reason")
    criterion, score = body.get("criterion"), body.get("score")
    if criterion not in load_config()["criteria"]:
        raise HTTPException(422, "unknown criterion")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise HTTPException(422, "score must be a whole number from 1 to 5")
    ref = str(body.get("agent_ref") or agent.get("name") or "")
    if not ref:
        raise HTTPException(422, "agent_ref is required")
    suggestions = suggest_scores(agent)
    before = suggestions[criterion]["score"]
    reason = reason.strip()
    suggestions[criterion] = {"score": score, "evidence": "override",
                              "reason": f"Override by {user.username}: {reason} (suggested {before})"}
    with db.engine.begin() as cx:
        _audit(cx, "criteria_override", ref,
               {"agent_ref": ref, "criterion": criterion, "from": before, "to": score, "reason": reason},
               actor=(user.user_id, user.username))
    return _result(suggestions)
