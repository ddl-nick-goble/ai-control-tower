"""POST /api/tiering/preview: read-only tier preview for the Register page (scores via backend/tiering.py)."""
import math

from fastapi import APIRouter, Body, HTTPException

from ..tiering import TieringInputRejected, score_tier

router = APIRouter()


@router.post("/api/tiering/preview")
def preview_tier(body=Body(...)):
    if not isinstance(body, dict) or not isinstance(body.get("criteria"), dict):
        raise HTTPException(422, "body must be {criteria: {...}}")
    crit = body["criteria"]
    if not crit:
        raise HTTPException(422, "criteria must not be empty")
    try:
        for k, v in crit.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                if not any(b in str(k).lower() for b in ("backtest", "benchmark")):
                    raise HTTPException(422, f"Criterion {k} must be a whole number from 1 to 5")
            elif not math.isfinite(v) or not float(v).is_integer():
                raise HTTPException(422, f"Criterion {k} must be a whole number from 1 to 5")
        res = score_tier(dict(crit))
    except HTTPException:
        raise
    except OverflowError:
        raise HTTPException(422, "Criteria must be whole numbers from 1 to 5")
    except (TieringInputRejected, ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    return {"tier": res.tier, "score": res.score, "reasoning": res.reasoning}
