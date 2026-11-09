"""Reconciliation assistant: reads two ledgers, calls fetch_ledger / compare_entries /
propose_adjustment, classifies breaks with the registered break_classifier model.
Proposals only; ACT receives the OTel spans as evidence."""
import json
import logging
import os

import requests

from agents.recon_agent import llm, tools
from agents.recon_agent.act_export import ActTraceBuffer
from agents.recon_agent.tracing_setup import add_tracing

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


def classify_break(br: dict) -> str:
    payload = {k: br.get(k, 0) for k in ("amount_diff", "days_diff", "missing_side")}
    url = os.environ.get("BREAK_CLASSIFIER_ENDPOINT_URL")
    if not url:
        from models.break_classifier.predict import predict
        return predict(payload)["class"]
    proxy = (os.environ.get("DOMINO_API_PROXY") or "http://localhost:8899").rstrip("/")
    tok = os.environ.get("BREAK_CLASSIFIER_TOKEN") or requests.get(f"{proxy}/access-token", timeout=5).text.strip()
    r = requests.post(url, json={"data": payload}, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()
    res = r.json()
    res = res.get("result", res)
    return str(res["class"]) if isinstance(res, dict) and "class" in res else str(res)


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
                br["classification"] = classify_break(br)
                a["gen_ai.tool.call.result"] = br["classification"]
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
                    out, err = {"error": str(e)}, True
                a["gen_ai.tool.call.result"] = json.dumps(out, default=str)[:2000]
            if u["name"] == "propose_adjustment" and not err:
                proposals.append(out)
            results.append({"type": "tool_result", "tool_use_id": u["id"],
                            "content": json.dumps(out, default=str), "is_error": err})
        messages.append({"role": "user", "content": results})
    return {"summary": text, "breaks": store.get("breaks", []), "proposals": proposals}


@add_tracing(name="recon_agent", span_type="AGENT")
def run(prompt: str) -> dict:
    buf = ActTraceBuffer()
    agent_id = os.environ.get("RECON_AGENT_ID", "recon-agent")
    try:
        with buf.invoke_agent(agent_id, AGENT_NAME) as a:
            a["gen_ai.agent.description"] = "Ledger reconciliation assistant"
            result = _run(prompt, buf)
    finally:
        buf.flush()
    return result
