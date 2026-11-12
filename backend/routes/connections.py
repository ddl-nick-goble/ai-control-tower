"""Connections API (PO-1): the backend of "Connect a provider". Route order matters: /callback and /template come
before /{id}."""
import os

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..connections import artifacts, store
from ..connections.paste import classify
from ..domino_identity import User, require_user

router = APIRouter()


def _first(value: str) -> str:
    return (value or "").split(",")[0].strip()


def _public_base(request: Request) -> str:
    """The App's public URL, no trailing slash. ACT_PUBLIC_URL wins (set it for a published App). Otherwise the
    forwarded host and proto (X-Forwarded-Host / X-Forwarded-Proto, else Host and the request scheme) plus the
    proxy prefix the middleware stripped (scope root_path, which covers the workspace preview
    .../notebookSession/<id>/proxy/<port>). With no prefix seen, DOMINO_RUN_HOST_PATH is appended."""
    explicit = (os.environ.get("ACT_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    h = request.headers
    proto = _first(h.get("x-forwarded-proto")) or request.url.scheme
    host = _first(h.get("x-forwarded-host")) or h.get("host") or request.url.netloc
    path = (request.scope.get("root_path") or "").rstrip("/")
    if not path:
        path = (os.environ.get("DOMINO_RUN_HOST_PATH") or "").rstrip("/")
    return f"{proto}://{host}{path}"


def _guard(fn, *a):
    try:
        return fn(*a)
    except store.ConnectionError_ as e:
        raise HTTPException(e.status, e.message)


def _found(x, cid):
    if x is None:
        raise HTTPException(404, "ACT has no connection with that id.")
    return x


@router.post("/api/connections")
def create_connection(request: Request, body: dict = Body(...), user: User = Depends(require_user)):
    provider = str(body.get("provider") or "").lower()
    row, token = _guard(store.create, provider, bool(body.get("sample")), user)
    return {"id": row["id"], "provider": provider, "state": row["state"], "sample": bool(row["sample"]),
            "state_token": token, "artifact": artifacts.build(provider, token, _public_base(request))}


@router.get("/api/connections")
def list_connections(_user: User = Depends(require_user)):
    return {"connections": store.list_all()}


@router.get("/api/connections/callback")
def callback(request: Request, state: str = ""):
    """The API behind the UI's callback page (the setup artifact's link lands on the SPA hash route, which calls this).
    Open to any tab: the single-use state token is the proof. STATE CHANGE ON GET: a valid, unused token moves the
    connection to trusted and stores the extra query values (role, project, account...) on it. A repeat with the
    same token on an already-trusted connection answers ok with a note and changes nothing."""
    ok, message, row = store.use_callback(state, {k: v for k, v in request.query_params.items()})
    return {"ok": ok, "message": message, "connection_id": row["id"] if row else None,
            "provider": row["provider"] if row else None, "state": row["state"] if row else None}


@router.get("/api/connections/template")
def template(request: Request, state: str = ""):
    """The CloudFormation template for a connection (what the quick-create link points at)."""
    row = store.template_for_state(state)
    if row is None or row["provider"] != "aws":
        raise HTTPException(404, "ACT doesn't recognise that setup link.")
    return PlainTextResponse(artifacts.render("aws", state, artifacts.build("aws", state, _public_base(request))["return_url"]))


@router.get("/api/connections/{cid}")
def get_connection(cid: str, _user: User = Depends(require_user)):
    return _found(store.get(cid), cid)


@router.delete("/api/connections/{cid}")
def delete_connection(cid: str, user: User = Depends(require_user)):
    return _found(store.disconnect(cid, user), cid)


@router.post("/api/connections/{cid}/paste")
def paste(cid: str, body: dict = Body(...), user: User = Depends(require_user)):
    provider = _found(store.provider_of(cid), cid)
    result = classify(str(body.get("text") or ""), provider)
    view = _guard(store.apply_paste, cid, result, user)
    return {**result, "connection": view}


@router.post("/api/connections/{cid}/check")
def check(cid: str, user: User = Depends(require_user)):
    return _found(_guard(store.run_check, cid, user), cid)


@router.get("/api/connections/{cid}/scopes")
def get_scopes(cid: str, _user: User = Depends(require_user)):
    return {"tree": _found(store.tree_for(cid), cid)}


@router.put("/api/connections/{cid}/scopes")
def put_scopes(cid: str, body: dict = Body(...), _user: User = Depends(require_user)):
    selected = body.get("selected")
    if not isinstance(selected, list):
        raise HTTPException(400, 'Send {"selected": [scope ids]}.')
    return {"tree": _found(_guard(store.save_scopes, cid, selected), cid)}
