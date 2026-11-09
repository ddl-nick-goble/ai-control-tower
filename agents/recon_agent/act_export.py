"""Direct ACT export: buffer spans for a run, POST OTLP/HTTP JSON at the end of the run.

MLflow < 3.11 cannot export OTLP itself, so the agent records spans here in OTel GenAI
conventions (invoke_agent, execute_tool, chat). Env:
  ACT_TRACES_URL  OTLP/HTTP traces endpoint; unset means export is a no-op
  ACT_TOKEN_URL   where to read the bearer token (default http://localhost:8899/access-token)
"""
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager

import requests

log = logging.getLogger(__name__)
DEFAULT_TOKEN_URL = "http://localhost:8899/access-token"
KIND = {"internal": 1, "server": 2, "client": 3}


def _val(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (dict, list, tuple)):
        v = json.dumps(v, default=str)
    return {"stringValue": str(v)}


def _attrs(d):
    return [{"key": k, "value": _val(v)} for k, v in d.items() if v is not None]


class ActTraceBuffer:
    def __init__(self, agent_id, service_name="act-recon-agent"):
        self.agent_id = agent_id
        self.service_name = service_name
        self.trace_id = secrets.token_hex(16)
        self.spans = []
        self._stack = []

    @contextmanager
    def span(self, name, attributes=None, kind="internal"):
        """Record a span; nested use sets parentSpanId. Yields the attribute dict (mutable)."""
        attrs = {"gen_ai.agent.id": self.agent_id, **(attributes or {})}
        sp = {"traceId": self.trace_id, "spanId": secrets.token_hex(8), "name": name,
              "kind": KIND[kind], "startTimeUnixNano": str(time.time_ns())}
        if self._stack:
            sp["parentSpanId"] = self._stack[-1]
        self._stack.append(sp["spanId"])
        status = {"code": 1}
        try:
            yield attrs
        except Exception as e:
            status = {"code": 2, "message": str(e)[:300]}
            attrs["error.type"] = type(e).__name__
            raise
        finally:
            self._stack.pop()
            sp["endTimeUnixNano"] = str(time.time_ns())
            sp["attributes"] = _attrs(attrs)
            sp["status"] = status
            self.spans.append(sp)

    def invoke_agent(self, agent_name, **attrs):
        return self.span(f"invoke_agent {agent_name}", {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent_name, **attrs})

    def execute_tool(self, tool_name, arguments=None, **attrs):
        return self.span(f"execute_tool {tool_name}", {
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool_name,
            "gen_ai.tool.call.arguments": json.dumps(arguments or {}, default=str), **attrs})

    def chat(self, model, **attrs):
        return self.span(f"chat {model}", {
            "gen_ai.operation.name": "chat", "gen_ai.request.model": model, **attrs}, kind="client")

    def payload(self):
        return {"resourceSpans": [{
            "resource": {"attributes": _attrs({"service.name": self.service_name,
                                                "gen_ai.agent.id": self.agent_id})},
            "scopeSpans": [{"scope": {"name": "act.recon_agent"}, "spans": self.spans}],
        }]}

    def flush(self) -> bool:
        """POST buffered spans synchronously. No-op (False) if ACT_TRACES_URL is unset or empty buffer.
        Never raises: an export failure must not fail the agent run."""
        url = os.environ.get("ACT_TRACES_URL")
        if not url or not self.spans:
            return False
        try:
            tr = requests.get(os.environ.get("ACT_TOKEN_URL", DEFAULT_TOKEN_URL), timeout=5)
            tok = tr.text.strip() if tr.status_code == 200 else ""
            if not tok:
                log.warning("ACT export skipped: no access token (token fetch HTTP %s)", tr.status_code)
                return False
            headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
            body = self.payload()
            r = None
            for attempt in (1, 2):  # one retry on POST failure
                try:
                    r = requests.post(url, json=body, timeout=30, headers=headers)
                    if r.status_code < 300:
                        break
                    log.warning("ACT export got HTTP %s (attempt %d)", r.status_code, attempt)
                except requests.RequestException as e:
                    log.warning("ACT export POST failed: %s (attempt %d)", type(e).__name__, attempt)
                    r = None
            ok = r is not None and r.status_code < 300
            if ok:
                self.spans = []
            return ok
        except Exception as e:  # noqa: BLE001
            log.warning("ACT export failed: %s", type(e).__name__)
            return False
