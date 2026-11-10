"""POST /api/agents/external: register a client-side A2A agent from its Agent Card URL."""
import json
import logging
from pathlib import Path

import jsonschema
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .. import db, external
from ..domino_identity import User, require_user
from ..normalize import _audit, apply_record
from ..tiering import TieringInputRejected, score_tier

router = APIRouter()
log = logging.getLogger(__name__)
SCHEMA = json.loads((Path(__file__).resolve().parent.parent.parent / "schemas" / "agent-record.json").read_text())


@router.post("/api/agents/external")
def register_external(body=Body(...), user: User = Depends(require_user)):
    if not isinstance(body, dict):
        raise HTTPException(422, "body must be {card_url, criteria}")
    try:
        url = external.check_url(body.get("card_url"))
        crit = body.get("criteria")
        if not isinstance(crit, dict):
            raise HTTPException(422, "criteria must be an object of the seven consequence answers")
        crit = dict(crit)
        scopes = crit.pop("data_scopes", None)
        try:
            res = score_tier(crit)
        except (TieringInputRejected, ValueError, TypeError) as e:
            raise HTTPException(422, str(e))
        aid = external.external_id(url)
        with db.engine.begin() as cx:
            row = cx.execute(select(db.agents).where(db.agents.c.id == aid)).first()
        if row is not None:
            return JSONResponse(row.record, status_code=200)
        card = external.fetch_card(url)
    except external.ExternalError as e:
        raise HTTPException(e.status, e.message)
    signature = external.verify_card(card)
    tier, reasoning = external.apply_floor(res.tier, res.reasoning, card)
    rec = external.build_record(url, card, {k: float(v) for k, v in crit.items()}, scopes, tier, reasoning,
                                signature, user)
    try:
        jsonschema.validate(rec, SCHEMA)
    except jsonschema.ValidationError as e:
        log.warning("external card produced an invalid record: %s", e.message)
        raise HTTPException(422, "the Agent Card does not contain the fields needed to build an agent record")
    actor = (user.user_id, user.username)
    with db.engine.begin() as cx:
        apply_record(cx, rec, actor=actor)
        _audit(cx, "external_registered", aid, {"card_url": url, "card_signature": signature, "tier": tier,
                                                "status": "pending_review"}, actor)
    return JSONResponse(rec, status_code=201)
