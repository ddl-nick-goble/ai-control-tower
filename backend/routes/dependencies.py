"""Dependency graph from the dependencies table."""
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import db

router = APIRouter()
_KEYS = {"model": "models", "mcp_server": "mcp_servers", "mcp": "mcp_servers", "tool": "tools"}


@router.get("/api/agents/{agent_id}/dependencies")
def dependencies(agent_id: str):
    out = {"models": [], "mcp_servers": [], "tools": []}
    with db.engine.connect() as cx:
        if cx.execute(select(db.agents.c.id).where(db.agents.c.id == agent_id)).first() is None:
            raise HTTPException(404, "agent not found")
        rows = cx.execute(select(db.dependencies).where(db.dependencies.c.agent_id == agent_id)
                          .order_by(db.dependencies.c.id)).all()
    for r in rows:
        rec = dict(r.record or {})
        item = {**rec, "name": r.name or rec.get("name"), "target": r.target, "kind": r.kind}
        if r.kind == "model":
            item["registry_version"] = rec.get("registry_version", r.target)
        out[_KEYS.get(r.kind, "tools")].append(item)
    return out
