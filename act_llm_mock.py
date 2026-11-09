"""Deterministic, offline stand-in for the LLM Gateway (ACT_LLM_PROVIDER=mock).

Shared by agents/recon_agent/llm.py and backend/llm.py (a root-level module so neither package imports the
other). It is NOT a model: it scripts the recon agent's tool loop, scores judge prompts by keyword overlap and
fills a template for drift explanations. Every response carries "model": "mock-llm" so records and the evidence
pack can label it honestly.
"""
import json
import re

MOCK_MODEL = "mock-llm"
_REF = re.compile(r"TXN-\d+", re.I)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z]{5,}")
_STOP = {"which", "should", "their", "there", "these", "those", "about", "would", "could", "other", "every",
         "while", "where", "being", "after", "before", "same", "needs", "need", "with", "that", "this"}


def _resp(content, stop, system_len=0):
    return {"stop_reason": stop, "content": content, "model": MOCK_MODEL, "id": "mock-msg",
            "usage": {"input_tokens": 0, "output_tokens": 0}}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _prompt_text(messages) -> str:
    return " ".join(_text(m.get("content")) for m in messages if m.get("role") == "user")


# ---- judge ---------------------------------------------------------------------------------------------
def _tokens(text: str):
    low = text.lower()
    refs = {r.upper() for r in _REF.findall(text)}
    nums = set(_NUM.findall(_REF.sub(" ", text)))
    words = {w for w in _WORD.findall(low) if w not in _STOP}
    return refs, nums, words


def judge(user_text: str) -> dict:
    m = re.search(r"<assistant_response>(.*?)</assistant_response>", user_text, re.S)
    resp = m.group(1) if m else ""
    e = re.search(r"Expected outcome:\n(.*?)\n\n<assistant_response>", user_text, re.S)
    exp = e.group(1) if e else ""
    er, en, ew = _tokens(exp)
    rr, rn, rw = _tokens(resp)
    parts = []
    for want, got, weight in ((er, rr, 0.5), (en, rn, 0.25), (ew, rw, 0.25)):
        if want:
            parts.append((weight, len(want & got) / len(want)))
    wsum = sum(w for w, _ in parts)
    score = sum(w * f for w, f in parts) / wsum if wsum else 0.0
    score = round(min(1.0, max(0.0, score)), 3)
    hit = len(er & rr)
    return {"score": score, "reason": f"mock keyword overlap: {hit} of {len(er)} expected refs, score {score}"}


# ---- recon tool loop -------------------------------------------------------------------------------------
def _calls(messages):
    """[(tool_use block, parsed tool_result or None)] in conversation order."""
    results = {}
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    try:
                        results[b.get("tool_use_id")] = json.loads(c) if isinstance(c, str) else c
                    except ValueError:
                        results[b.get("tool_use_id")] = c
    out = []
    for m in messages:
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    out.append((b, results.get(b.get("id"))))
    return out


def _size(b):
    return max(float(b.get("amount_diff") or 0), 0.0) or (float(b.get("amount") or 0) if b.get("missing_side") else 0.0)


def _describe(b):
    cls = b.get("classification") or b.get("kind")
    if b.get("kind") == "missing_entry":
        side = "custodian" if b.get("missing_in") == "b" else "GL"
        return f"{b['ref']} missing in the {side} (amount {b.get('amount')}, class {cls})"
    if b.get("kind") == "timing":
        return f"{b['ref']} timing difference of {b.get('days_diff')} days, amounts equal (class {cls})"
    return f"{b['ref']} amount mismatch, difference {float(b.get('amount_diff') or 0):.2f} (class {cls})"


def _recon_step(messages):
    calls = _calls(messages)
    n = len(calls)
    names = [c[0]["name"] for c in calls]
    fetched = [(c[0].get("input") or {}).get("name") for c in calls if c[0]["name"] == "fetch_ledger"]

    def use(name, inp):
        return _resp([{"type": "text", "text": f"Step {n + 1}: calling {name}."},
                      {"type": "tool_use", "id": f"toolu_mock_{n + 1}", "name": name, "input": inp}], "tool_use")
    if "gl_ledger" not in fetched:
        return use("fetch_ledger", {"name": "gl_ledger"})
    if "custodian_ledger" not in fetched:
        return use("fetch_ledger", {"name": "custodian_ledger"})
    if "compare_entries" not in names:
        return use("compare_entries", {"ledger_a": "gl_ledger", "ledger_b": "custodian_ledger"})
    breaks = next((r for b, r in calls if b["name"] == "compare_entries" and isinstance(r, list)), [])
    breaks = [b for b in breaks if isinstance(b, dict) and b.get("ref")]
    top = max(breaks, key=_size) if breaks else None
    if top and "propose_adjustment" not in names:
        return use("propose_adjustment", {"break_id": str(top.get("break_id", "1")), "ref": top["ref"],
                                          "amount": _size(top),
                                          "reason": f"largest break: {_describe(top)}"})
    if not breaks:
        return _resp([{"type": "text", "text": "Compared the GL and custodian ledgers: no breaks found."}], "end_turn")
    prop = f" Proposed an adjustment for the largest break {top['ref']} (proposal only, nothing executed)." \
        if "propose_adjustment" in names else ""
    txt = (f"Compared the GL and custodian ledgers: {len(breaks)} breaks. "
           + "; ".join(_describe(b) for b in breaks) + "." + prop)
    return _resp([{"type": "text", "text": txt}], "end_turn")


# ---- drift explanation ---------------------------------------------------------------------------------------
def _drift(prompt: str) -> str:
    tool = (re.search(r"Tool:\s*([^.\s]+(?:\.[^.\s]+)*)\.\s", prompt) or re.search(r"Tool:\s*(\S+)", prompt))
    reason = re.search(r"Reason:\s*([a-z_]+)", prompt)
    t, r = (tool.group(1) if tool else "an unknown tool"), (reason.group(1) if reason else "unspecified")
    return f"The agent called {t} outside its approved scope (reason: {r.replace('_', ' ')})."


# ---- entry point -----------------------------------------------------------------------------------------------
def create_message(model_alias, messages, tools=None, system=None, max_tokens=1024, tags=None, timeout=120,
                   **extra) -> dict:
    sys_text = _text(system) if not isinstance(system, str) else system
    prompt = _prompt_text(messages)
    if "<assistant_response>" in (sys_text or "") or "strict evaluator" in (sys_text or ""):
        out = _resp([{"type": "text", "text": json.dumps(judge(prompt))}], "end_turn")
    elif tools and any(t.get("name") == "fetch_ledger" for t in tools):
        out = _recon_step(messages)
    elif "scope drift" in prompt.lower() or "Tool:" in prompt:
        out = _resp([{"type": "text", "text": _drift(prompt)}], "end_turn")
    else:
        out = _resp([{"type": "text", "text": "mock-llm: offline stand-in, not a model."}], "end_turn")
    out["alias"] = model_alias
    return out
