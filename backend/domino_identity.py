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
        claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"],
                            options={"verify_aud": False})
    except Exception:
        return None
    uid = claims.get("sub")
    name = claims.get("preferred_username") or claims.get("email") or uid
    return User(user_id=str(uid), username=str(name)) if uid else None


def require_user(request: Request) -> User:
    uid = request.headers.get("X-Domino-User-Id")
    name = request.headers.get("X-Domino-User")
    if uid and name:
        return User(user_id=uid, username=name)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        user = _verify_bearer(auth[7:].strip())
        if user:
            return user
    raise HTTPException(status_code=401, detail="Authentication required")
