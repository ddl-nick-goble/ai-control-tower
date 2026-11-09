import sys
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _req(token):
    return types.SimpleNamespace(headers={"Authorization": f"Bearer {token}"})


def test_t1_bearer_jwt_accepts_valid_rejects_foreign_key(monkeypatch):
    good, other = _key(), _key()
    monkeypatch.setenv("DOMINO_JWKS_URL", "https://kc.invalid/jwks")
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    from backend import domino_identity as di

    class StubClient:
        def __init__(self, url, *a, **kw):
            pass

        def get_signing_key_from_jwt(self, token):
            return types.SimpleNamespace(key=good.public_key())

    monkeypatch.setattr(jwt, "PyJWKClient", StubClient)
    monkeypatch.setattr(di, "_jwks_client", None)

    claims = {"sub": "u-123", "preferred_username": "alice"}
    ok = jwt.encode(claims, good, algorithm="RS256")
    user = di.require_user(_req(ok))
    assert user.user_id == "u-123" and user.username == "alice"

    bad = jwt.encode(claims, other, algorithm="RS256")
    with pytest.raises(HTTPException) as e:
        di.require_user(_req(bad))
    assert e.value.status_code == 401
