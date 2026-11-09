"""Scope-drift detection: compare a run's tool calls with the agent's manifest.

ACT detects and records only; it never blocks anything in the agent's runtime.
"""
import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from . import db

log = logging.getLogger(__name__)
SEVERITY = {1: "critical", 2: "high", 3: "medium", 4: "low"}
LLM_WAIT_SECONDS = 2.0
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tier(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 4


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            f = float(v)
            return int(f) if f == int(f) else f
        except ValueError:
            return None
    return None


def _limit(v):
    if isinstance(v, dict):
        v = v.get("amount")
    return _num(v)


def _amount(ev: dict, raw: dict):
    srcs = [ev.get("args_redacted"), ev.get("attrs")]
    a = (raw or {}).get("attributes") or {}
    for k in ("gen_ai.tool.call.arguments", "tool.parameters", "mlflow.spanInputs", "input.value"):
        v = a.get(k)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = None
        srcs.append(v)
    for s in srcs:
        if isinstance(s, dict):
            n = _num(s.get("amount"))
            if n is not None:
                return n
    return None


def _manifest(conn, agent_id: str) -> dict | None:
    row = conn.execute(select(db.agents.c.record).where(db.agents.c.id == agent_id)).first()
    m = ((row.record or {}) if row else {}).get("manifest")
    if not m:
        r = conn.execute(select(db.manifests.c.content).where(db.manifests.c.agent_id == agent_id)
                         .order_by(db.manifests.c.created_at.desc())).first()
        m = r.content if r else None
        if isinstance(m, str):
            m = json.loads(m)
    return m


def find_violations(events, manifest: dict) -> list[dict]:
    allowed = set(manifest.get("tools") or [])
    limits = dict(manifest.get("value_limits") or {})
    for k, v in (manifest.get("value_limits_detail") or {}).items():
        limits.setdefault(k, v)
    out = []
    for e in events:
        ev = e.event or {}
        if ev.get("kind") != "tool_call":
            continue
        tool = ev.get("name")
        base = {"tool": tool, "span_id": e.span_id or ev.get("span_id"), "ts": e.ts or ev.get("ts")}
        if tool not in allowed:
            out.append({**base, "reason": "tool_not_in_manifest", "observed_value": None, "limit": None})
            continue
        lim = _limit(limits.get(tool))
        amt = _amount(ev, e.raw_span)
        if lim is not None and amt is not None and amt > lim:
            out.append({**base, "reason": "value_limit_exceeded", "observed_value": amt, "limit": lim})
    out.sort(key=lambda v: (v["ts"] or "", v["span_id"] or ""))
    return out


def fallback_explanation(v: dict, agent_name: str, tier: int) -> str:
    who = f"Agent {agent_name} (Tier {tier})"
    if v["reason"] == "value_limit_exceeded":
        return (f"{who} called {v['tool']} with value {v['observed_value']}, above its approved "
                f"limit of {v['limit']}.")
    return f"{who} called {v['tool']}, which is not in its approved manifest."


def _llm_explanation(v: dict, agent_name: str, tier: int):
    from . import llm
    prompt = (f"In one plain sentence for a bank risk officer, explain this AI agent scope drift. "
              f"Agent: {agent_name} (Tier {tier}). Tool: {v['tool']}. Reason: {v['reason']}. "
              f"Observed value: {v['observed_value']}. Limit: {v['limit']}. Time: {v['ts']}.")
    r = llm.create_message("haiku", [{"role": "user", "content": prompt}], max_tokens=120,
                           tags={"component": "act-drift"}, timeout=20)
    text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise ValueError("empty LLM response")
    return text, r.get("model")


def _fill_explanation(fid: int, v: dict, agent_name: str, tier: int, fb: str, done: threading.Event):
    try:
        text, model = _llm_explanation(v, agent_name, tier)
        with _lock, db.engine.begin() as conn:
            row = conn.execute(select(db.findings.c.detail).where(db.findings.c.id == fid)).first()
            d = dict(row.detail or {})
            if d.get("explanation") == fb:  # not changed since
                d.update(explanation=text, explanation_model=model)
                conn.execute(update(db.findings).where(db.findings.c.id == fid)
                             .values(detail=d, message=text))
    except Exception:
        log.warning("drift explanation LLM failed; keeping fallback", exc_info=True)
    finally:
        done.set()


def detect_run(run_id: str) -> int | None:
    """Idempotently create/update the scope_drift finding for a run. Returns finding id."""
    with _lock, db.engine.begin() as conn:
        evs = conn.execute(select(db.events.c.agent_id, db.events.c.ts, db.events.c.span_id,
                                  db.events.c.raw_span, db.events.c.event)
                           .where(db.events.c.run_id == run_id)).all()
        agent_id = next((e.agent_id for e in evs if e.agent_id and e.agent_id != "unknown"), None)
        if not agent_id:
            return None
        ag = conn.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        man = _manifest(conn, agent_id) if ag else None
        if man is None:
            return None
        viol = find_violations(evs, man)
        if not viol:
            return None
        tier = _tier(ag.tier)
        first = viol[0]
        fb = fallback_explanation(first, ag.name or agent_id, tier)
        cur = conn.execute(select(db.findings).where(
            db.findings.c.run_id == run_id, db.findings.c.type == "scope_drift")).first()
        vals = dict(agent_id=agent_id, run_id=run_id, severity=SEVERITY.get(tier, "low"),
                    rule="scope_drift", type="scope_drift", tool=first["tool"], span_id=first["span_id"],
                    ts=first["ts"], reason=first["reason"],
                    observed_value=None if first["observed_value"] is None else str(first["observed_value"]),
                    limit=None if first["limit"] is None else str(first["limit"]))
        detail = {"violations": viol, "tier": tier}
        need_llm = True
        if cur is None:
            detail.update(explanation=fb, explanation_model=None)
            conn.execute(insert(db.findings).values(**vals, message=fb, status="open",
                                                    created_at=_now(), detail=detail))
            fid = conn.execute(select(db.findings.c.id).where(
                db.findings.c.run_id == run_id, db.findings.c.type == "scope_drift")).scalar()
        else:
            fid = cur.id
            old = dict(cur.detail or {})
            same = (cur.tool, cur.span_id, cur.reason) == (first["tool"], first["span_id"], first["reason"])
            if same and old.get("explanation_model"):
                need_llm = False
            detail.update(explanation=old.get("explanation") if same else fb,
                          explanation_model=old.get("explanation_model") if same else None)
            if not same or not old.get("explanation"):
                detail["explanation"] = fb
            fb = detail["explanation"]
            conn.execute(update(db.findings).where(db.findings.c.id == fid)
                         .values(**vals, detail=detail, message=detail["explanation"]))
    if need_llm:
        done = threading.Event()
        threading.Thread(target=_fill_explanation, daemon=True,
                         args=(fid, first, ag.name or agent_id, tier, fb, done)).start()
        done.wait(LLM_WAIT_SECONDS)
    return fid
