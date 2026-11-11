"""POST /api/tiering/preview: read-only tier preview for the Register page (scores via backend/tiering.py)."""
import math

import json

from fastapi import APIRouter, HTTPException, Request

from ..tiering import TieringInputRejected, _short, score_tier

router = APIRouter()
MAX_KEYS = 32
MAX_BYTES = 8 * 1024


@router.post("/api/tiering/preview")
async def preview_tier(request: Request):
    raw = b""
    async for chunk in request.stream():  # cap enforced here too (chunked bodies carry no Content-Length)
        raw += chunk
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, "Request body too large (limit 8 KB)")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "body must be {criteria: {...}}")
    if not isinstance(body, dict) or not isinstance(body.get("criteria"), dict):
        raise HTTPException(422, "body must be {criteria: {...}}")
    crit = body["criteria"]
    if len(crit) > MAX_KEYS:
        raise HTTPException(422, f"criteria must have at most {MAX_KEYS} keys")
    if not crit:
        raise HTTPException(422, "criteria must not be empty")
    try:
        for k, v in crit.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                if not any(b in str(k).lower() for b in ("backtest", "benchmark")):
                    raise HTTPException(422, f"Criterion {_short(k)} must be a whole number from 1 to 5")
            elif not math.isfinite(v) or not float(v).is_integer():
                raise HTTPException(422, f"Criterion {_short(k)} must be a whole number from 1 to 5")
        res = score_tier(dict(crit))
    except HTTPException:
        raise
    except OverflowError:
        raise HTTPException(422, "Criteria must be whole numbers from 1 to 5")
    except (TieringInputRejected, ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    return {"tier": res.tier, "score": res.score, "reasoning": res.reasoning}
