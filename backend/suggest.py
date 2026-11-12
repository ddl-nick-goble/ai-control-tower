"""Suggested criteria scores (PO-2). Deterministic: rules in config/criteria_rules.yaml, no randomness, no LLM."""
import os
import pathlib
import re

import yaml

DEFAULT_RULES = pathlib.Path(__file__).resolve().parent.parent / "config" / "criteria_rules.yaml"


def load_rules():
    return yaml.safe_load(pathlib.Path(os.environ.get("ACT_CRITERIA_RULES") or DEFAULT_RULES).read_text())["criteria"]


def _facts(agent):
    agent = agent if isinstance(agent, dict) else {}
    tools = []
    for t in agent.get("tools") or []:
        name = t.get("name") if isinstance(t, dict) else t
        if name:
            tools.append(str(name))
    limits = agent.get("value_limits") if isinstance(agent.get("value_limits"), dict) else {}
    identity = agent.get("identity") if isinstance(agent.get("identity"), dict) else {}
    extra = agent.get("extra") if isinstance(agent.get("extra"), dict) else {}
    return {"tools": sorted(set(tools)), "scopes": sorted({str(s) for s in agent.get("data_scopes") or []}),
            "systems": sorted({str(s) for s in agent.get("systems") or []}), "limits": limits,
            "distinct": identity.get("distinct") is True, "hitl": extra.get("human_in_loop") is True}


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
        hit = [t for t in f["tools"] if verbs & set(re.split(r"[_\-.\s]+", t.lower()))]
        if not hit:
            return None
        ev += hit
    if "scope_contains" in rule:
        words = rule["scope_contains"]
        hit = [s for s in f["scopes"] if any(w in s.lower() for w in words)
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
        hit = [s for s in f["systems"] if any(w in s.lower() for w in rule["systems_contain"])]
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
            out[crit] = {"score": int(default), "reason": "no evidence found", "evidence": ""}
        else:
            rule, ev = best
            out[crit] = {"score": int(rule["score"]), "evidence": ev,
                         "reason": f"{ev} {rule['why']} -> {crit.replace('_', ' ')} {int(rule['score'])}"}
    return out
