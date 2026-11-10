"""POST /api/tiering/preview: read-only tier preview for the Register page (scores via backend/tiering.py)."""
from fastapi import APIRouter, Body, Depends, HTTPException

from ..domino_identity import User, require_user
from ..tiering import TieringInputRejected, score_tier

router = APIRouter()


@router.post("/api/tiering/preview")
def preview_tier(body=Body(...), user: User = Depends(require_user)):
    if not isinstance(body, dict) or not isinstance(body.get("criteria"), dict):
        raise HTTPException(422, "body must be {criteria: {...}}")
    crit = body["criteria"]
    if not crit:
        raise HTTPException(422, "criteria must not be empty")
    for k, v in crit.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            if not any(b in str(k).lower() for b in ("backtest", "benchmark")):
                raise HTTPException(422, f"Criterion {k} must be a whole number from 1 to 5")
        elif float(v) != int(v):
            raise HTTPException(422, f"Criterion {k} must be a whole number from 1 to 5")
    try:
        res = score_tier(dict(crit))
    except (TieringInputRejected, ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    return {"tier": res.tier, "score": res.score, "reasoning": res.reasoning}
