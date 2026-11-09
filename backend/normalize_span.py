"""Normalize OTel GenAI / OpenInference / MLflow spans to the canonical event."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

_CFG = Path(__file__).resolve().parent.parent / "config" / "redaction.yaml"
_cfg_cache = None


def _config():
    global _cfg_cache
    if _cfg_cache is None:
        c = yaml.safe_load(_CFG.read_text()) or {}
        _cfg_cache = {
            "replacement": c.get("replacement", "[REDACTED]"),
            "keys": {k.lower() for k in c.get("keys", []) if k.lower() != "amount"},
            "key_patterns": [re.compile(p) for p in c.get("key_patterns", [])],
            "patterns": [re.compile(p["regex"]) for p in c.get("patterns", [])],
        }
    return _cfg_cache


def redact(obj, key=None):
    c = _config()
    if key is not None and str(key).lower() != "amount":
        k = str(key)
        if k.lower() in c["keys"] or any(p.search(k) for p in c["key_patterns"]):
            return c["replacement"]
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        for p in c["patterns"]:
            obj = p.sub(c["replacement"], obj)
    return obj


def _maybe_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def iso(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


AGENT_KEYS = ("gen_ai.agent.id", "agent.id", "openinference.agent.id", "mlflow.agent.id")


def native_agent_id(attrs: dict, resource: dict):
    """Return (native_id, explicit) where explicit is False if only service.name was found."""
    for src in (attrs, resource):
        for k in AGENT_KEYS:
            if src.get(k):
                return str(src[k]), True
    if resource.get("service.name"):
        return str(resource["service.name"]), False
    return None, False


def _kind(name: str, a: dict) -> str:
    explicit = a.get("act.event.kind")
    if explicit in ("input", "model_call", "tool_call", "output", "approval", "override"):
        return explicit
    op = str(a.get("gen_ai.operation.name") or "").lower()
    if op:
        if op in ("invoke_agent", "create_agent"):
            return "input"
        if op == "execute_tool":
            return "tool_call"
        if op in ("approval", "override"):
            return op
        return "model_call"
    oi = str(a.get("openinference.span.kind") or "").upper()
    if oi:
        return {"AGENT": "input", "CHAIN": "input", "LLM": "model_call", "EMBEDDING": "model_call",
                "TOOL": "tool_call"}.get(oi, "tool_call")
    ml = str(a.get("mlflow.spanType") or "").strip('"').upper()
    if ml:
        return {"AGENT": "input", "CHAIN": "input", "LLM": "model_call", "CHAT_MODEL": "model_call",
                "TOOL": "tool_call"}.get(ml, "tool_call")
    n = name.lower()
    if n.startswith("invoke_agent"):
        return "input"
    if n.startswith("execute_tool"):
        return "tool_call"
    if n.startswith("chat") or n.startswith("text_completion"):
        return "model_call"
    return "tool_call"


def normalize_span(span: dict, agent_id: str | None, native_id: str | None = None) -> dict:
    a = span.get("attributes", {})
    kind = _kind(span.get("name", ""), a)
    name = a.get("gen_ai.tool.name") or a.get("tool.name") or span.get("name") or kind
    if kind == "model_call":
        name = a.get("gen_ai.request.model") or span.get("name") or name
    raw_args = None
    for k in ("gen_ai.tool.call.arguments", "tool.parameters", "mlflow.spanInputs", "input.value",
              "gen_ai.prompt"):
        if k in a:
            raw_args = _maybe_json(a[k])
            break
    skip = set(AGENT_KEYS) | {"gen_ai.tool.call.arguments", "tool.parameters", "mlflow.spanInputs"}
    attrs = {k: _maybe_json(v) for k, v in a.items() if k not in skip}
    attrs["service.name"] = (span.get("resource") or {}).get("service.name")
    if native_id:
        attrs["native_agent_id"] = native_id
    if span.get("end_ns") and span.get("start_ns"):
        attrs["duration_ns"] = span["end_ns"] - span["start_ns"]
    ev = {
        "run_id": span["trace_id"],
        "agent_id": agent_id or "unknown",
        "ts": iso(span.get("start_ns", 0)),
        "kind": kind,
        "name": str(name),
        "span_id": span.get("span_id", ""),
        "parent_span_id": span.get("parent_span_id"),
        "args_redacted": redact(raw_args),
        "attrs": redact({k: v for k, v in attrs.items() if v is not None}),
    }
    return ev
