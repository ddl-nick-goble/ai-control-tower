"""Caller identity: Domino proxy headers or a bearer JWT verified against Keycloak JWKS."""
import os
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class User:
    user_id: str
    username: str
    via: str = "domino"  # "domino" (proxy headers or bearer) | "workspace" (workspace owner mode)


_jwks_client = None


def _verify_bearer(token: str):
    url = os.environ.get("DOMINO_JWKS_URL")
    if not url:
        return None
    global _jwks_client
    try:
        import jwt
        if _jwks_client is None or getattr(_jwks_client, "_url", None) != url:
            _jwks_client = jwt.PyJWKClient(url)
            _jwks_client._url = url
        key = _jwks_client.get_signing_key_from_jwt(token)
        kwargs = {"algorithms": ["RS256", "ES256"],
                  "options": {"require": ["exp", "sub"], "verify_aud": False}}
        if os.environ.get("DOMINO_JWT_ISSUER"):
            kwargs["issuer"] = os.environ["DOMINO_JWT_ISSUER"]
        if os.environ.get("DOMINO_JWT_AUDIENCE"):
            kwargs["audience"] = os.environ["DOMINO_JWT_AUDIENCE"]
            kwargs["options"]["verify_aud"] = True
        claims = jwt.decode(token, key.key, **kwargs)
    except Exception:
        return None
    uid = claims.get("sub")
    name = claims.get("preferred_username")
    return User(user_id=str(uid), username=str(name)) if uid and name else None


def workspace_owner_mode() -> bool:
    """True when an anonymous caller is the project owner: the app runs inside a Domino Workspace, where only the
    workspace owner can reach the port. ACT_WORKSPACE_OWNER_MODE=1 forces on, 0 forces off. Unset: on only when
    DOMINO_RUN_HOST_PATH contains "/notebookSession/" (a Workspace, not a published App or Job) AND
    DOMINO_PROJECT_OWNER is set. Any other value, or anything missing, is off (fail closed)."""
    owner = (os.environ.get("DOMINO_PROJECT_OWNER") or "").strip()
    if not owner:
        return False
    forced = (os.environ.get("ACT_WORKSPACE_OWNER_MODE") or "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    if forced:
        return False
    return "/notebookSession/" in (os.environ.get("DOMINO_RUN_HOST_PATH") or "")


def get_user(request):
    """Return the caller or None. A valid bearer wins over proxy headers.

    ASSUMPTION: X-Domino-User-Id / X-Domino-User are trusted only because the Domino
    proxy is the sole ingress to this app (it strips/sets them). Never expose the app
    port directly, or any client could forge identity.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        user = _verify_bearer(auth[7:].strip())
        if user:
            return user
    uid = request.headers.get("X-Domino-User-Id")
    name = request.headers.get("X-Domino-User")
    if uid and name:
        return User(user_id=uid, username=name)
    # Owner mode only for a caller that presented no credentials at all; a rejected bearer or a half set of
    # identity headers is never upgraded to the owner.
    if not auth and not uid and not name and workspace_owner_mode():
        owner = os.environ["DOMINO_PROJECT_OWNER"].strip()
        return User(user_id=f"workspace:{owner}", username=owner, via="workspace")
    return None


def require_user(request: Request) -> User:
    user = get_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
