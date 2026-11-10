"""Reconciliation assistant: reads two ledgers, calls fetch_ledger / compare_entries /
propose_adjustment, classifies breaks with the registered break_classifier model.
Proposals only; ACT receives the OTel spans as evidence."""
import json
import logging
import os
import re
import sys
import threading

import requests

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.recon_agent import llm, tools
from agents.recon_agent.act_export import ActTraceBuffer
from tracing_setup import add_tracing, agent_run

log = logging.getLogger(__name__)
AGENT_NAME = "recon-agent"
MAX_STEPS = 8
SYSTEM = ("You are a reconciliation assistant. Fetch both ledgers, compare them, then propose an "
          "adjustment for each break (proposals only, nothing is executed). Be concise.")

TOOL_SPECS = [
    {"name": "fetch_ledger", "description": "Load a fixture ledger by name (gl_ledger or custodian_ledger).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "compare_entries", "description": "Compare two ledgers (by name) and list breaks.",
     "input_schema": {"type": "object", "properties": {"ledger_a": {"type": "string"},
                                                       "ledger_b": {"type": "string"}},
                      "required": ["ledger_a", "ledger_b"]}},
    {"name": "propose_adjustment", "description": "Propose a journal entry for a break.",
     "input_schema": {"type": "object", "properties": {"break_id": {"type": "string"},
                                                       "reason": {"type": "string"},
                                                       "amount": {"type": "number"},
                                                       "ref": {"type": "string"}},
                      "required": ["break_id", "reason"]}},
]


UNCLASSIFIED = "unclassified"
_registry_cache = {}
_registry_lock = threading.Lock()


def _payload(br):
    return {k: br.get(k, 0) for k in ("amount_diff", "days_diff", "missing_side")}


def _from_endpoint(url, payload):
    key = os.environ.get("BREAK_CLASSIFIER_ACCESS_KEY")
    if not key:
        raise RuntimeError("BREAK_CLASSIFIER_ACCESS_KEY is required when BREAK_CLASSIFIER_ENDPOINT_URL is set")
    # Domino sync endpoints use HTTP Basic with the endpoint access key as user and password.
    r = requests.post(url, json={"data": payload}, auth=(key, key), timeout=30)
    r.raise_for_status()
    res = r.json().get("result")
    if isinstance(res, (list, tuple)):  # registry (MLflow pyfunc) endpoints return {"result": ["<class>"]}
        res = res[0] if res else None
    return str(res["class"] if isinstance(res, dict) else res)


_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_HOST_RE = re.compile(
    r"""\bhost=(['"])[^'"]*\1"""                         # host='name' / host="name"
    r"|\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:/\S*)?"       # IPv4 address, optional port and path
    r"|\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?(?:/\S*)?")  # dotted hostname
REGISTRY_TIMEOUT_S = 8.0


def _safe_msg(e):
    """Exception message with URLs and hostnames removed."""
    msg = _HOST_RE.sub("<host>", _URL_RE.sub("<url>", str(e)))
    return msg.replace("\n", " ")[:120]


def _note(step, e):
    """Sanitised failure note: step name, exception type and a URL-free message."""
    return f"{step} failed ({type(e).__name__}: {_safe_msg(e)})"


def _load_registry_model():
    import mlflow.pyfunc
    wanted = {"MLFLOW_HTTP_REQUEST_TIMEOUT": "5", "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "0"}
    set_here = [k for k in wanted if k not in os.environ]
    for k in set_here:
        os.environ[k] = wanted[k]
    try:
        return mlflow.pyfunc.load_model("models:/break_classifier@champion")
    finally:
        for k in set_here:
            os.environ.pop(k, None)


def _registry_model():
    """Load the registry model at most once per process (failures are memoised) within a wall-clock cap."""
    if "m" in _registry_cache:  # fast path without the lock
        return _registry_cache["m"]
    if "err" in _registry_cache:
        raise _registry_cache["err"]
    with _registry_lock:  # re-check inside it so only one thread loads
        if "m" in _registry_cache:
            return _registry_cache["m"]
        if "err" in _registry_cache:
            raise _registry_cache["err"]
        return _load_registry_once()


def _load_registry_once():
    box = {}

    def work():
        try:
            box["m"] = _load_registry_model()
        except BaseException as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(REGISTRY_TIMEOUT_S)
    if "m" in box:
        _registry_cache["m"] = box["m"]
        return box["m"]
    err = box.get("err")
    if err is None:
        err = TimeoutError(f"registry lookup exceeded {REGISTRY_TIMEOUT_S:g}s")
    _registry_cache["err"] = err
    raise err


def _from_registry(payload):
    """Best effort: MLflow registry models:/break_classifier@champion."""
    model = _registry_model()
    import pandas as pd
    out = model.predict(pd.DataFrame([payload]))
    out = list(out)[0] if hasattr(out, "__len__") else out
    return str(out["class"] if isinstance(out, dict) else out)


def classify_break(br: dict, notes=None):
    """Return (class, source). Order: endpoint (URL+key) -> BREAK_CLASSIFIER_MODEL_PATH -> MLflow registry
    models:/break_classifier@champion -> ("unclassified", "unavailable"). Never raises: a classifier
    failure must not hide the break; reasons are appended to `notes`."""
    payload = _payload(br)
    notes = notes if notes is not None else []
    url = os.environ.get("BREAK_CLASSIFIER_ENDPOINT_URL")
    if url:
        try:
            return _from_endpoint(url, payload), "endpoint"
        except Exception as e:  # noqa: BLE001
            notes.append(_note("endpoint", e))
    if os.environ.get("BREAK_CLASSIFIER_MODEL_PATH"):
        try:
            from models.break_classifier.predict import predict
            return predict(payload)["class"], "local"
        except Exception as e:  # noqa: BLE001
            notes.append(_note("model path", e))
    try:
        return _from_registry(payload), "registry"
    except Exception as e:  # noqa: BLE001
        notes.append(_note("registry", e))
    return UNCLASSIFIED, "unavailable"


def _dispatch(name, inp, buf, store):
    if name == "fetch_ledger":
        out = tools.fetch_ledger(inp["name"])
        store[inp["name"]] = out
        return out
    if name == "compare_entries":
        breaks = tools.compare_entries(inp["ledger_a"], inp["ledger_b"])
        for br in breaks:
            with buf.execute_tool("break_classifier", {k: br[k] for k in
                                  ("amount_diff", "days_diff", "missing_side")}) as a:
                notes = []
                br["classification"], a["act.classifier.source"] = classify_break(br, notes)
                a["gen_ai.tool.call.result"] = br["classification"]
                if notes:
                    a["act.classifier.error"] = "; ".join(notes)[:500]
                    store.setdefault("warnings", [])
                    for n in notes:
                        w = f"break classifier unavailable: {n}"
                        if w not in store["warnings"]:
                            store["warnings"].append(w)
        store["breaks"] = breaks
        return breaks
    if name == "propose_adjustment":
        return tools.propose_adjustment(**inp)
    raise ValueError(f"unknown tool {name}")


def _run(prompt: str, buf: ActTraceBuffer) -> dict:
    messages = [{"role": "user", "content": prompt}]
    store, proposals, text = {}, [], ""
    for _ in range(MAX_STEPS):
        with buf.chat("sonnet") as ca:
            resp = llm.create_message("sonnet", messages, tools=TOOL_SPECS, system=SYSTEM)
            ca["gen_ai.response.model"] = resp.get("model") or "sonnet"
            usage = resp.get("usage") or {}
            ca["gen_ai.usage.input_tokens"] = usage.get("input_tokens")
            ca["gen_ai.usage.output_tokens"] = usage.get("output_tokens")
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text") or text
        uses = [b for b in content if b.get("type") == "tool_use"]
        if resp.get("stop_reason") != "tool_use" or not uses:
            break
        results = []
        for u in uses:
            with buf.execute_tool(u["name"], u.get("input")) as a:
                try:
                    out = _dispatch(u["name"], u.get("input") or {}, buf, store)
                    err = False
                except Exception as e:  # noqa: BLE001
                    out, err = {"error": _note(u["name"], e)}, True
                a["gen_ai.tool.call.result"] = json.dumps(out, default=str)[:2000]
            if u["name"] == "propose_adjustment" and not err:
                proposals.append(out)
            results.append({"type": "tool_result", "tool_use_id": u["id"],
                            "content": json.dumps(out, default=str), "is_error": err})
        messages.append({"role": "user", "content": results})
    warnings = store.get("warnings", [])
    if warnings:  # surfaced in the output; breaks are reported regardless
        text = (text.rstrip() + "\nWarning: " + "\nWarning: ".join(warnings)).strip()
    return {"summary": text, "breaks": store.get("breaks", []), "proposals": proposals, "warnings": warnings}


@add_tracing(name="recon_agent", span_type="AGENT")
def _traced_run(prompt: str, agent_id: str) -> dict:
    buf = ActTraceBuffer(agent_id)
    try:
        with buf.invoke_agent(AGENT_NAME) as a:
            a["gen_ai.agent.description"] = "Ledger reconciliation assistant"
            return _run(prompt, buf)
    finally:
        buf.flush()


def run(prompt: str) -> dict:
    agent_id = os.environ.get("RECON_AGENT_ID", "").strip()
    if not agent_id:
        raise RuntimeError("RECON_AGENT_ID is not set: it must equal the Domino agent's native id "
                           "(see agents/recon_agent/README.md)")
    with agent_run():
        return _traced_run(prompt, agent_id)
