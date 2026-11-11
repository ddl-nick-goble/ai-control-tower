"""Single entry point for LLM calls: Domino LLM Gateway 2.0, Anthropic-compatible path.

Gateway base URL comes from env LLM_GATEWAY_URL (the gateway App's URL, for example
https://<apps host>/apps/<vanity>); nothing is hardcoded and no provider key is used.
Model names are gateway aliases (for example "sonnet", "haiku"). The in-run access token is
read fresh from $DOMINO_API_PROXY/access-token (default http://localhost:8899) on every call.
"""
import logging
import os

import requests

from agents.recon_agent import config

log = logging.getLogger(__name__)
_warned = False

DEFAULT_PROXY = "http://localhost:8899"
ANTHROPIC_VERSION = "2023-06-01"


class GatewayError(RuntimeError):
    pass


def warn_if_mock() -> None:
    """Log once per process that LLM calls are answered by the offline mock, not a model."""
    global _warned
    if not _warned and (config.get("llm_provider") or "gateway").strip().lower() == "mock":
        _warned = True
        log.warning("ACT_LLM_PROVIDER=mock: LLM calls are answered by the offline mock-llm stand-in, "
                    "NOT a model. Unset it and set LLM_GATEWAY_URL to use the LLM Gateway.")


def _token() -> str:
    proxy = (os.environ.get("DOMINO_API_PROXY") or DEFAULT_PROXY).rstrip("/")
    try:
        tok = requests.get(f"{proxy}/access-token", timeout=5).text.strip()
    except requests.RequestException:
        tok = ""
    if not tok:  # outside Domino: a Domino bearer the user stored
        tok = os.environ.get("LLM_GATEWAY_TOKEN", "")
    if not tok:
        raise GatewayError("no Domino access token (in-run proxy unreachable, LLM_GATEWAY_TOKEN unset)")
    return tok


def gateway_url() -> str:
    url = (config.get("llm_gateway_url") or "").rstrip("/")
    if not url:
        raise GatewayError("LLM_GATEWAY_URL is not set (Project env var: the LLM Gateway App URL)")
    return url


def _headers(tags=None) -> dict:
    h = {"Authorization": f"Bearer {_token()}", "anthropic-version": ANTHROPIC_VERSION,
         "content-type": "application/json"}
    for k, v in (tags or {}).items():
        h[f"X-LLM-Tag-{k}"] = str(v)
    return h


def list_aliases() -> list:
    """Aliases the caller may use (GET <base>/v1/models, data[].id)."""
    r = requests.get(f"{gateway_url()}/v1/models", headers=_headers(), timeout=30, allow_redirects=False)
    _check(r)
    return [m["id"] for m in r.json().get("data", [])]


def _check(r) -> None:
    if 300 <= r.status_code < 400:
        raise GatewayError(
            f"gateway redirected ({r.status_code}) to {r.headers.get('location', '?').split('?')[0]}: "
            "bearer missing/expired, or the user has not consented to the gateway App "
            "(open the gateway URL in a browser once and accept)")
    if r.status_code >= 400:
        raise GatewayError(f"gateway {r.status_code}: {r.text[:500]}")


def create_message(model_alias, messages, tools=None, system=None, max_tokens=1024,
                   tags=None, timeout=120, **extra) -> dict:
    """Call POST <gateway>/anthropic/v1/messages and return an Anthropic-style dict.

    Returns {"stop_reason", "content": [{"type": "text"|"tool_use", ...}], "model", "usage", "id"}.
    "model" is the model id the gateway reports having served (alias resolved upstream).
    """
    if (config.get("llm_provider") or "gateway").strip().lower() == "mock":
        warn_if_mock()
        from act_llm_mock import create_message as _mock  # deterministic offline stand-in, model "mock-llm"
        return _mock(model_alias, messages, tools=tools, system=system, max_tokens=max_tokens, tags=tags,
                     timeout=timeout, **extra)
    body = {"model": model_alias, "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    if system:
        body["system"] = system
    body.update(extra)
    r = requests.post(f"{gateway_url()}/anthropic/v1/messages", json=body,
                      headers=_headers(tags), timeout=timeout, allow_redirects=False)
    _check(r)
    d = r.json()
    return {
        "stop_reason": d.get("stop_reason"),
        "content": d.get("content", []),
        "model": d.get("model"),
        "usage": d.get("usage", {}),
        "id": d.get("id"),
        "alias": model_alias,
    }
