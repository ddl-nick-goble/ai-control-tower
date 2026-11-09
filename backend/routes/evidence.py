"""Evidence pack API: generate, list, download."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from .. import db, evidence
from ..domino_identity import User, require_user

router = APIRouter()


@router.post("/api/agents/{agent_id}/evidence-pack")
def create_pack(agent_id: str, user: User = Depends(require_user)):
    try:
        return evidence.generate(agent_id, user.user_id, user.username)
    except evidence.AgentNotFound:
        raise HTTPException(404, "agent not found")


def _item(r):
    return {"pack_id": r.id, "agent_id": r.agent_id, "created_at": r.created_at, "created_by": r.created_by,
            "markdown_path": r.path, "pdf_path": r.path[:-3] + ".pdf" if r.path.endswith(".md") else None}


@router.get("/api/agents/{agent_id}/evidence-packs")
def list_packs(agent_id: str):
    with db.engine.connect() as cx:
        if cx.execute(select(db.agents.c.id).where(db.agents.c.id == agent_id)).first() is None:
            raise HTTPException(404, "agent not found")
        rows = cx.execute(select(db.evidence_packs).where(db.evidence_packs.c.agent_id == agent_id)
                          .order_by(db.evidence_packs.c.id.desc())).all()
    return [_item(r) for r in rows]


def _serve(pack_id: int, ext: str, media: str):
    with db.engine.connect() as cx:
        r = cx.execute(select(db.evidence_packs).where(db.evidence_packs.c.id == pack_id)).first()
    if r is None or not r.path:
        raise HTTPException(404, "evidence pack not found")
    p = evidence.Path(r.path).with_suffix(ext).resolve()
    root = evidence.evidence_dir().resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(p, media_type=media, filename=p.name)


@router.get("/api/evidence-packs/{pack_id}/markdown")
def get_md(pack_id: int):
    return _serve(pack_id, ".md", "text/markdown; charset=utf-8")


@router.get("/api/evidence-packs/{pack_id}/pdf")
def get_pdf(pack_id: int):
    return _serve(pack_id, ".pdf", "application/pdf")
