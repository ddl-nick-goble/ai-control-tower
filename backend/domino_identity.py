"""Caller identity: Domino proxy headers or a bearer JWT verified against Keycloak JWKS."""
import os
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass
class User:
    user_id: str
    username: str


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
    return None


def require_user(request: Request) -> User:
    user = get_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
