"""Federation helper (PO-1.7): turn a fresh Domino token into a provider credential.

Pure functions build the request and parse the answer, so they are unit-tested without HTTP. `exchange()` runs them
for real (live use waits for a test account). The Domino token lives 300 s: fetch one per exchange, never cache it.
"""
import json
import os
from urllib.parse import quote, urlencode

TOKEN_URL = "http://localhost:8899/access-token"


def domino_token(get=None) -> str:
    """A fresh Domino token from the local token endpoint (`get` is injectable for tests)."""
    if get is None:
        import httpx
        get = lambda u: httpx.get(u, timeout=5).text  # noqa: E731
    return get(os.environ.get("ACT_DOMINO_TOKEN_URL") or TOKEN_URL).strip().strip('"')


def _form(d: dict) -> str:
    return urlencode(d)


FORM = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}


def build_exchange_request(provider: str, domino_token: str, cfg: dict):
    """-> (method, url, headers, body). cfg keys: aws {role_arn, region?}; google {audience, service_account?};
    microsoft {tenant_id, client_id}; databricks {host, client_id?}; snowflake {account}."""
    t = domino_token
    if provider == "aws":
        region = cfg.get("region") or "us-east-1"
        body = _form({"Action": "AssumeRoleWithWebIdentity", "Version": "2011-06-15", "RoleArn": cfg["role_arn"],
                      "RoleSessionName": "act-scan", "WebIdentityToken": t, "DurationSeconds": 3600})
        return "POST", f"https://sts.{region}.amazonaws.com/", dict(FORM), body
    if provider == "google":
        body = {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "audience": cfg["audience"],
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt", "subject_token": t}
        return "POST", "https://sts.googleapis.com/v1/token", {"Content-Type": "application/json"}, body
    if provider == "microsoft":
        body = _form({"client_id": cfg["client_id"], "grant_type": "client_credentials",
                      "scope": cfg.get("scope") or "https://management.azure.com/.default",
                      "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                      "client_assertion": t})
        return "POST", f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/token", dict(FORM), body
    if provider == "databricks":
        f = {"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "subject_token": t,
             "subject_token_type": "urn:ietf:params:oauth:token-type:jwt", "scope": "all-apis"}
        if cfg.get("client_id"):
            f["client_id"] = cfg["client_id"]
        return "POST", cfg["host"].rstrip("/") + "/oidc/v1/token", dict(FORM), _form(f)
    if provider == "snowflake":
        # No exchange: Snowflake takes the Domino token itself as a WIF bearer on every SQL API call.
        host = f"https://{cfg['account'].lower()}.snowflakecomputing.com/api/v2/statements"
        headers = {"Authorization": f"Bearer WIF.OIDC.{t}",
                   "X-Snowflake-Authorization-Token-Type": "WORKLOAD_IDENTITY_FEDERATION",
                   "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ACT/1.0"}
        return "POST", host, headers, {"statement": "SELECT CURRENT_USER()"}
    raise ValueError(f"unknown provider {provider}")


def build_impersonation_request(access_token: str, service_account: str):
    """Google step 2: the federated token impersonates the read-only service account."""
    url = ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
           f"{quote(service_account, safe='@.')}:generateAccessToken")
    body = {"scope": ["https://www.googleapis.com/auth/cloud-platform"]}
    return "POST", url, {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, body


def parse_exchange_response(provider: str, payload: dict) -> dict:
    """-> {"token", "expires_in"} or, for AWS, {"credentials": {...}, "expires_in"}."""
    if provider == "aws":
        cred = payload["AssumeRoleWithWebIdentityResponse"]["AssumeRoleWithWebIdentityResult"]["Credentials"]
        return {"credentials": {"access_key_id": cred["AccessKeyId"], "secret_access_key": cred["SecretAccessKey"],
                                "session_token": cred["SessionToken"]},
                "expires_in": 3600, "expiration": cred.get("Expiration")}
    token = payload.get("access_token") or payload.get("accessToken") or payload.get("token")
    if not token:
        raise ValueError(f"{provider} answered without a token")
    return {"token": token, "expires_in": int(payload.get("expires_in") or 3600)}


def _send(method, url, headers, body):
    import httpx
    kw = {"json": body} if isinstance(body, dict) else {"content": body}
    r = httpx.request(method, url, headers=headers, timeout=20, **kw)
    r.raise_for_status()
    return r.json()


def exchange(provider: str, cfg: dict, send=None, token_getter=None) -> dict:
    """Fetch a fresh Domino token and trade it for a provider credential. `send` and `token_getter` are injectable."""
    send = send or _send
    tok = (token_getter or domino_token)()
    if provider == "snowflake":
        return {"token": f"WIF.OIDC.{tok}", "expires_in": 300}
    out = parse_exchange_response(provider, send(*build_exchange_request(provider, tok, cfg)))
    if provider == "google" and cfg.get("service_account"):
        out = parse_exchange_response(provider, send(*build_impersonation_request(out["token"], cfg["service_account"])))
    return out


# One function per provider, as the AC asks.
def aws(cfg, **kw): return exchange("aws", cfg, **kw)
def google(cfg, **kw): return exchange("google", cfg, **kw)
def microsoft(cfg, **kw): return exchange("microsoft", cfg, **kw)
def databricks(cfg, **kw): return exchange("databricks", cfg, **kw)
def snowflake(cfg, **kw): return exchange("snowflake", cfg, **kw)
