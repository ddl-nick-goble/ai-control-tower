"""Settings API (T-39). Admins only: the project owner plus the "admins" setting."""
import os
from fastapi import APIRouter, Body, Depends, HTTPException

from sqlalchemy import func, select

from .. import db, settings
from ..domino_identity import User, require_user

router = APIRouter()


def require_admin(user: User = Depends(require_user)) -> User:
    if not settings.is_admin(user):
        raise HTTPException(403, "Settings are limited to the project owner and listed admins"
                            if settings.admins_configured() else
                            "Nobody can open Settings yet: no project owner or admins are configured")
    return user


@router.get("/api/settings/access")
def access(user: User = Depends(require_user)):
    return {"admin": settings.is_admin(user), "admins_configured": settings.admins_configured(),
            "user": user.username, "via": user.via,
            "owner": (os.environ.get("DOMINO_PROJECT_OWNER") or "").strip()}


@router.get("/api/settings/real-agent-count")
def real_agent_count(_user: User = Depends(require_admin)):
    """Agents in the real (non-demo) DB, so the demo-off confirm can say what will be shown."""
    with db.real_engine.connect() as cx:
        return {"real_agent_count": cx.execute(select(func.count()).select_from(db.agents)).scalar() or 0}


@router.get("/api/settings")
def list_settings(_user: User = Depends(require_admin)):
    return settings.snapshot()


def _call(fn, *a):
    try:
        return fn(*a)
    except settings.SettingError as e:
        raise HTTPException(e.status, e.message)


@router.put("/api/settings/{name}")
def put_setting(name: str, body: dict = Body(default=None), user: User = Depends(require_admin)):
    if name not in settings.DEFS:
        raise HTTPException(404, "unknown setting")
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(422, "body must be {\"value\": ...}")
    return _call(settings.put_setting, user, name, body["value"])


@router.delete("/api/settings/{name}")
def delete_setting(name: str, user: User = Depends(require_admin)):
    return _call(settings.delete_setting, user, name)
