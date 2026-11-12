"""PO-1.7 federation helper: backend/connections/federation.py (pure functions, no live HTTP).

Contract:
  build_exchange_request(provider, domino_token, cfg) -> (method, url, headers, body)
  parse_exchange_response(provider, payload) -> {"token" or "credentials", "expires_in"}
cfg keys per provider:
  aws {role_arn, region?}; google {audience (WIF provider path), service_account?};
  microsoft {tenant_id, client_id}; databricks {host, client_id?}; snowflake {account}
body is str/bytes/dict; the Domino token must appear in it (or in headers for snowflake bearer).
"""
import json
from urllib.parse import parse_qs

import pytest

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhY3Qtc3ZjIn0.c2ln"
CFG = {
    "aws": {"role_arn": "arn:aws:iam::123456789012:role/ActReadOnly", "region": "us-east-1"},
    "google": {"audience": "//iam.googleapis.com/projects/483920174563/locations/global/workloadIdentityPools/p/providers/d",
               "service_account": "act@proj.iam.gserviceaccount.com"},
    "microsoft": {"tenant_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "client_id": "9b2c1d4e-1111-4222-8333-444455556666"},
    "databricks": {"host": "https://dbc-a1b2c3d4-e5f6.cloud.databricks.com", "client_id": "abc"},
    "snowflake": {"account": "myorg-myaccount"},
}


@pytest.fixture
def fed():
    from backend.connections import federation
    return federation


def _blob(body):
    if isinstance(body, bytes):
        body = body.decode()
    if isinstance(body, dict):
        body = json.dumps(body)
    return body


@pytest.mark.parametrize("provider", list(CFG))
def test_ac7_request_carries_token_and_target(fed, provider):
    method, url, headers, body = fed.build_exchange_request(provider, TOKEN, CFG[provider])
    assert method in ("GET", "POST") and url.startswith("https://")
    assert TOKEN in _blob(body) + json.dumps(headers)


def test_ac7_aws_sts(fed):
    _, url, _, body = fed.build_exchange_request("aws", TOKEN, CFG["aws"])
    assert "sts" in url and "amazonaws.com" in url
    b = _blob(body)
    assert "AssumeRoleWithWebIdentity" in b + url and "WebIdentityToken" in b + url
    assert "role%2FActReadOnly" in b + url or "role/ActReadOnly" in b + url


def test_ac7_google_sts(fed):
    _, url, _, body = fed.build_exchange_request("google", TOKEN, CFG["google"])
    assert "sts.googleapis.com" in url
    assert "token-exchange" in _blob(body)


def test_ac7_microsoft_client_assertion(fed):
    _, url, _, body = fed.build_exchange_request("microsoft", TOKEN, CFG["microsoft"])
    assert "login.microsoftonline.com/" + CFG["microsoft"]["tenant_id"] in url
    b = _blob(body)
    assert "client_assertion" in b and "jwt-bearer" in b and CFG["microsoft"]["client_id"] in b


def test_ac7_databricks_oidc(fed):
    _, url, _, body = fed.build_exchange_request("databricks", TOKEN, CFG["databricks"])
    assert url.endswith("/oidc/v1/token") and url.startswith(CFG["databricks"]["host"])
    assert "token-exchange" in _blob(body)


def test_ac7_snowflake_bearer(fed):
    _, url, headers, body = fed.build_exchange_request("snowflake", TOKEN, CFG["snowflake"])
    assert "myorg-myaccount" in url or "myorg-myaccount" in _blob(body)


@pytest.mark.parametrize("provider,payload,key", [
    ("aws", {"AssumeRoleWithWebIdentityResponse": {"AssumeRoleWithWebIdentityResult": {"Credentials": {
        "AccessKeyId": "ASIAXXX", "SecretAccessKey": "s", "SessionToken": "t", "Expiration": 1893456000}}}}, "credentials"),
    ("google", {"access_token": "ya29.abc", "token_type": "Bearer", "expires_in": 3599}, "token"),
    ("microsoft", {"access_token": "eyJ0.az", "token_type": "Bearer", "expires_in": 3599}, "token"),
    ("databricks", {"access_token": "dapi-oidc", "token_type": "Bearer", "expires_in": 3600}, "token"),
    ("snowflake", {"access_token": "sf-tok", "token_type": "Bearer", "expires_in": 3600}, "token"),
])
def test_ac7_parse_response(fed, provider, payload, key):
    out = fed.parse_exchange_response(provider, payload)
    assert key in out and out[key] and "expires_in" in out
