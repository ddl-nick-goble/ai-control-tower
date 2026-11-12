"""Settings API (T-39). Admins only: the project owner plus the "admins" setting."""
import os
import sqlite3
import threading
import time
from pathlib import Path
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, settings
from ..domino_identity import User, require_user

router = APIRouter()
_count_lock = threading.Lock()


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


_COUNT_TTL = 10.0
_count_cache = {"path": None, "at": -1e9, "n": None}


def _read_real_count(path: Path):
    """Count agents through a read-only connection so nothing is created or written."""
    cx = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    try:
        return cx.execute("SELECT COUNT(*) FROM agents").fetchone()[0] or 0
    finally:
        cx.close()


@router.get("/api/settings/real-agent-count")
def real_agent_count(_user: User = Depends(require_admin)):
    """Agents in the real (non-demo) DB, so the demo-off confirm can say what will be shown.
    0 when there is no real database yet; null when it cannot be read."""
    path = Path(db.REAL_DB_PATH).expanduser()
    if not path.exists():  # connecting would make SQLite create a stray empty database file
        return {"real_agent_count": 0}
    key = str(path)
    with _count_lock:
        if _count_cache["path"] == key and time.monotonic() - _count_cache["at"] < _COUNT_TTL:
            return {"real_agent_count": _count_cache["n"]}
    try:
        n = _read_real_count(path)
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        n = None
    except Exception:  # noqa: BLE001
        n = None
    with _count_lock:
        _count_cache.update(path=key, at=time.monotonic(), n=n)
    return {"real_agent_count": n}


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
