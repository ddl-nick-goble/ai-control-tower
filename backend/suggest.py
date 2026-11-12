"""Suggested criteria scores (PO-2). Deterministic: rules in config/criteria_rules.yaml, no randomness, no LLM."""
import os
import pathlib
import re

import yaml

DEFAULT_RULES = pathlib.Path(__file__).resolve().parent.parent / "config" / "criteria_rules.yaml"


def load_rules():
    return yaml.safe_load(pathlib.Path(os.environ.get("ACT_CRITERIA_RULES") or DEFAULT_RULES).read_text())["criteria"]


READ_VERBS = {"read", "get", "list", "view", "describe", "fetch", "search", "query"}


def _norm(s):
    return re.sub(r"[-_.\s]+", "", str(s).lower())


def _words_hit(text, words):
    """Normalised substring match; words of 3 chars or fewer must equal a whole token (gl is not global)."""
    n = _norm(text)
    toks = set(re.split(r"[-_.:/\s]+", str(text).lower()))
    return any((w in toks) if len(w) <= 3 else (_norm(w) in n) for w in words)


def _verbs(tool):
    toks = [t for t in re.split(r"[_\-.\s]+", tool.lower()) if t]
    if toks and toks[0] in READ_VERBS:
        return {toks[0]}
    return set(toks)


def _dict(v):
    return v if isinstance(v, dict) else {}


def _facts(agent):
    agent = agent if isinstance(agent, dict) else {}
    man = _dict(agent.get("manifest"))

    def pick(key, alt=None):
        v = agent.get(key)
        if not v and man:
            v = man.get(alt or key)
        return v

    tools = []
    for t in pick("tools") or []:
        name = t.get("name") if isinstance(t, dict) else t
        if name:
            tools.append(str(name))
    limits = pick("value_limits")
    limits = limits if isinstance(limits, dict) else {}
    identity = _dict(agent.get("identity"))
    hitl = None
    for src in (_dict(agent.get("extra")), _dict(agent.get("criteria")), _dict(man.get("permissions"))):
        if isinstance(src.get("human_in_loop"), bool):
            hitl = src["human_in_loop"]
            break
    scopes = pick("data_scopes") or []
    systems = pick("systems") or []
    return {"tools": sorted(set(tools)), "scopes": sorted({str(s) for s in scopes}),
            "systems": sorted({str(s) for s in systems}), "limits": limits,
            "distinct": identity.get("distinct") is True, "hitl": hitl is True}


def _amount(v):
    a = v.get("amount") if isinstance(v, dict) else v
    return a if isinstance(a, (int, float)) and not isinstance(a, bool) else None


def _money(v):
    cur = v.get("currency", "USD") if isinstance(v, dict) else "USD"
    return f"${_amount(v):,.0f}" if cur == "USD" else f"{_amount(v):,.0f} {cur}"


def _match(rule, f):
    """Return the evidence string if every condition in the rule holds, else None."""
    ev = []
    if "tool_verbs" in rule:
        verbs = set(rule["tool_verbs"])
        hit = [t for t in f["tools"] if verbs & _verbs(t)]
        if not hit:
            return None
        ev += hit
    if "scope_contains" in rule:
        words = rule["scope_contains"]
        hit = [s for s in f["scopes"] if _words_hit(s, words)
               and (rule.get("scope_access") != "write" or s.lower().endswith(":write"))]
        if not hit:
            return None
        ev += hit
    if "value_limit_min" in rule:
        hit = [f"{t} can move up to {_money(v)}" for t, v in sorted(f["limits"].items())
               if _amount(v) is not None and _amount(v) >= rule["value_limit_min"]]
        if not hit:
            return None
        ev += hit
    if "systems_contain" in rule:
        hit = [s for s in f["systems"] if _words_hit(s, rule["systems_contain"])]
        if not hit:
            return None
        ev += hit
    if rule.get("identity_distinct") is True:
        if not f["distinct"]:
            return None
        ev.append("distinct identity")
    if "human_in_loop" in rule:
        if f["hitl"] != rule["human_in_loop"]:
            return None
        ev.append("human_in_loop=true" if f["hitl"] else "no human_in_loop")
    return ", ".join(ev)


def suggest_scores(agent: dict) -> dict:
    f = _facts(agent)
    out = {}
    for crit, spec in load_rules().items():
        best = None
        for rule in spec.get("rules") or []:
            ev = _match(rule, f)
            if ev is not None and (best is None or int(rule["score"]) > best[0]["score"]):
                best = (rule, ev)
        if best is None:
            default = spec.get("default", spec.get("default_score", 2))
            out[crit] = {"score": int(default), "reason": f"{spec.get('checked', 'No matching tool, scope, system or setting')}; default {int(default)} -> {crit.replace('_', ' ')} {int(default)}", "evidence": ""}
        else:
            rule, ev = best
            out[crit] = {"score": int(rule["score"]), "evidence": ev,
                         "reason": f"{ev} {rule['why']} -> {crit.replace('_', ' ')} {int(rule['score'])}"}
    return out
