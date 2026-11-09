import sys
import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

ISS = "https://kc.invalid/realms/domino"
AUD = "act"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _req(token, extra=None):
    h = {"Authorization": f"Bearer {token}"}
    h.update(extra or {})
    return types.SimpleNamespace(headers=h)


def _claims(**over):
    c = {"sub": "u-123", "preferred_username": "alice", "iss": ISS, "aud": AUD,
         "exp": int(time.time()) + 600}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


@pytest.fixture
def setup(monkeypatch):
    good = _key()
    monkeypatch.setenv("DOMINO_JWKS_URL", "https://kc.invalid/jwks")
    monkeypatch.setenv("DOMINO_JWT_ISSUER", ISS)
    monkeypatch.setenv("DOMINO_JWT_AUDIENCE", AUD)
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
    return di, good


def _enc(key, claims):
    return jwt.encode(claims, key, algorithm="RS256")


def _rejects(di, tok):
    with pytest.raises(HTTPException) as e:
        di.require_user(_req(tok))
    assert e.value.status_code == 401


def test_t1_bearer_jwt_accepts_valid_rejects_foreign_key(setup):
    di, good = setup
    user = di.require_user(_req(_enc(good, _claims())))
    assert user.user_id == "u-123" and user.username == "alice"
    _rejects(di, _enc(_key(), _claims()))


def test_t1_bearer_jwt_wrong_issuer_401(setup):
    di, good = setup
    _rejects(di, _enc(good, _claims(iss="https://evil.invalid")))


def test_t1_bearer_jwt_wrong_audience_401(setup):
    di, good = setup
    _rejects(di, _enc(good, _claims(aud="other")))


def test_t1_bearer_jwt_missing_exp_401(setup):
    di, good = setup
    _rejects(di, _enc(good, _claims(exp=None)))


def test_t1_bearer_jwt_missing_sub_401(setup):
    di, good = setup
    _rejects(di, _enc(good, _claims(sub=None)))


def test_t1_bearer_jwt_audience_unset_not_checked_issuer_still(setup, monkeypatch):
    di, good = setup
    monkeypatch.delenv("DOMINO_JWT_AUDIENCE")
    user = di.require_user(_req(_enc(good, _claims(aud="anything"))))
    assert user.user_id == "u-123"
    _rejects(di, _enc(good, _claims(iss="https://evil.invalid")))


def test_t1_bearer_precedence_over_headers(setup):
    di, good = setup
    req = _req(_enc(good, _claims()),
               {"X-Domino-User-Id": "someone-else", "X-Domino-User": "mallory"})
    user = di.require_user(req)
    assert user.user_id == "u-123" and user.username == "alice"
