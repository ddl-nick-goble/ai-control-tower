"""Scope-drift detection: compare a run's tool calls with the agent's manifest.

ACT detects and records only; it never blocks anything in the agent's runtime.
"""
import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import insert, select, update

from . import db
from .settings import get_setting

log = logging.getLogger(__name__)
SEVERITY = {1: "critical", 2: "high", 3: "medium", 4: "low"}
LLM_WAIT_SECONDS = 2.0
_lock = threading.Lock()
_inflight: set[int] = set()  # finding ids with an explanation thread running
_batch = threading.local()  # per-ingest-request LLM wait budget (hooks run in the request thread)
BATCH_GAP_SECONDS = 1.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tier(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 4


_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_COMMA_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_ISO_PRE = re.compile(r"^([A-Za-z]{3})\s*(?=[\d.+-])")
_ISO_SUF = re.compile(r"(?<=[\d.])\s*([A-Za-z]{3})$")
_SYMBOL_ISO = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "\u00a5": "JPY"}
# Fixed set of active ISO-4217 codes; anything else is not a currency.
ISO_4217 = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD
GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR
NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP
STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF
XPF YER ZAR ZMW ZWL
""".split())
_MISSING = object()
_MAX_TEXT = 200


def _clip(s, n: int = _MAX_TEXT):
    return s if not isinstance(s, str) or len(s) <= n else s[:n]


class Unverifiable(Exception):
    def __init__(self, note: str):
        note = _clip(note)
        super().__init__(note)
        self.note = note


def _iso(code) -> str:
    """Normalise a currency token to ISO-4217 or raise Unverifiable."""
    c = code.strip().upper() if isinstance(code, str) else ""
    if c in ISO_4217:
        return c
    raise Unverifiable("amount carries an unrecognised currency or unit token")


def parse_amount(v):
    """Return (number, iso_currency_or_None). Raise Unverifiable for anything not a finite number.

    Commas are accepted only as thousands grouping (^\\d{1,3}(,\\d{3})+(\\.\\d+)?$). An optional
    currency is an ISO-4217 code or one of $ EUR-sign GBP-sign JPY-sign; any other alphabetic token
    (mil, k, M, XYZ ...) is unverifiable.
    """
    ccy = None
    try:
        if isinstance(v, bool) or v is None:
            raise Unverifiable("amount is not a number")
        if isinstance(v, str):
            t = v.strip()
            m = _ISO_PRE.match(t)
            if m:
                ccy, t = _iso(m.group(1)), t[m.end():]
            else:
                m = _ISO_SUF.search(t)
                if m:
                    ccy, t = _iso(m.group(1)), t[:m.start()]
            t = t.strip()
            if t and t[0] in _SYMBOL_ISO:
                sym = _SYMBOL_ISO[t[0]]
                if ccy and ccy != sym:
                    raise Unverifiable("amount carries conflicting currency tokens")
                ccy, t = sym, t[1:].strip()
            if "," in t:
                if not _COMMA_RE.match(t):
                    raise Unverifiable("amount has an invalid comma form")
                t = t.replace(",", "")
            if not _NUM_RE.match(t):
                raise Unverifiable("amount could not be parsed as a number")
            f = float(t)
        elif isinstance(v, (int, float)):
            f = float(v)
        else:
            raise Unverifiable("amount is not a number")
    except (ValueError, OverflowError, TypeError):
        raise Unverifiable("amount could not be parsed as a number")
    if not math.isfinite(f):
        raise Unverifiable("amount is not finite")
    return (int(f) if f == int(f) else f), ccy


def _num(v):
    """Lenient numeric read for stored limits; None when unusable."""
    try:
        return parse_amount(v)[0]
    except Unverifiable:
        return None


def _limit(v):
    if isinstance(v, dict):
        v = v.get("amount")
    return _num(v)


def _limit_ccy(v):
    """Currency of a stored limit. A bare limit (number/string without currency) carries none and is
    compared numerically against any call currency."""
    if isinstance(v, dict) and isinstance(v.get("currency"), str):
        return v["currency"].strip().upper() or None
    return None


def _amount_args(ev: dict, raw: dict):
    """Return (amount_raw_or__MISSING, currency_or_None) from the first source carrying an amount key."""
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
        if isinstance(s, dict) and "amount" in s:
            return s["amount"], s.get("currency")
    return _MISSING, None


def _jsonable(v):
    if isinstance(v, (str, int, dict, list)) or v is None:
        return v
    return str(v)


def _raw_text(raw):
    r = _jsonable(raw) if not (isinstance(raw, float) and not math.isfinite(raw)) else str(raw)
    if isinstance(r, (dict, list)):
        j = json.dumps(r, default=str)
        return r if len(j) <= _MAX_TEXT else j[:_MAX_TEXT]
    return _clip(r)


def _manifest(conn, agent_id: str) -> dict | None:
    r = conn.execute(select(db.manifests.c.content).where(db.manifests.c.agent_id == agent_id)
                     .order_by(db.manifests.c.created_at.desc(), db.manifests.c.id.desc())).first()
    m = r.content if r else None
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            m = None
    if not m:
        row = conn.execute(select(db.agents.c.record).where(db.agents.c.id == agent_id)).first()
        m = ((row.record or {}) if row else {}).get("manifest")
    if not m:
        log.warning("no manifest found for agent %s; drift not evaluated", agent_id)
        return None
    return m


def _declared_call(ev, tool, declared) -> bool:
    """A call to a declared model dependency that carries the act.classifier.source marker."""
    return bool(declared) and tool in declared and "act.classifier.source" in (ev.get("attrs") or {})


def _span_violations(e, allowed, limits, declared=()) -> list[dict]:
    ev = e.event or {}
    if ev.get("kind") != "tool_call":
        return []
    tool = ev.get("name")
    if tool not in allowed and _declared_call(ev, tool, declared):
        allowed = set(allowed) | {tool}
    base = {"tool": tool, "span_id": e.span_id or ev.get("span_id"), "ts": e.ts or ev.get("ts")}
    if tool not in allowed:
        return [{**base, "reason": "tool_not_in_manifest", "observed_value": None, "limit": None}]
    lim_raw = limits.get(tool)
    lim = _limit(lim_raw)
    if lim is None:
        return []
    raw, call_ccy = _amount_args(ev, e.raw_span)
    if raw is _MISSING:
        return []
    unv = {**base, "reason": "value_unverifiable", "observed_value": None, "limit": lim,
           "raw_value": _raw_text(raw)}
    try:
        amt, str_ccy = parse_amount(raw)
    except Unverifiable as u:
        return [{**unv, "note": u.note}]
    lccy = _limit_ccy(lim_raw)
    try:
        cc = _iso(call_ccy) if isinstance(call_ccy, str) and call_ccy.strip() else None
        if cc and str_ccy and cc != str_ccy:
            raise Unverifiable("currency tokens in the call disagree")
    except Unverifiable as u:
        return [{**unv, "note": u.note}]
    for c in (cc, str_ccy):
        if c and lccy and c != lccy:
            return [{**unv, "note": _clip(f"currency {c} differs from limit currency {lccy}")}]
    if amt > lim:
        return [{**base, "reason": "value_limit_exceeded", "observed_value": amt, "limit": lim}]
    return []


def allowed_declared_calls(events, declared) -> list[dict]:
    out = []
    for e in events:
        ev = e.event or {}
        if ev.get("kind") == "tool_call" and _declared_call(ev, ev.get("name"), declared):
            out.append({"tool": ev.get("name"), "span_id": e.span_id or ev.get("span_id"),
                        "ts": e.ts or ev.get("ts")})
    return out


def find_violations(events, manifest: dict, declared=()) -> list[dict]:
    allowed = set(manifest.get("tools") or [])
    limits = dict(manifest.get("value_limits") or {})
    for k, v in (manifest.get("value_limits_detail") or {}).items():
        limits.setdefault(k, v)
    out = []
    for e in events:
        try:
            out.extend(_span_violations(e, allowed, limits, declared))
        except Exception:  # one bad span must never drop the run's other violations
            log.warning("drift check failed for span %s", getattr(e, "span_id", None), exc_info=True)
            ev = e.event or {}
            if ev.get("kind") == "tool_call":
                out.append({"tool": ev.get("name"), "span_id": e.span_id or ev.get("span_id"),
                            "ts": e.ts or ev.get("ts"), "reason": "value_unverifiable",
                            "observed_value": None, "limit": None, "note": "detector error"})
    out.sort(key=lambda v: (v["ts"] or "", v["span_id"] or ""))
    return out


def fallback_explanation(v: dict, agent_name: str, tier: int) -> str:
    who = f"Agent {agent_name} (Tier {tier})"
    if v["reason"] == "value_limit_exceeded":
        return (f"{who}: {v['tool']} amount {v['observed_value']} exceeds the manifest limit "
                f"{v['limit']} (approved limit exceeded).")
    if v["reason"] == "value_unverifiable":
        return (f"{who} called {v['tool']} with a value that could not be verified against its "
                f"approved limit of {v['limit']} ({v.get('note', 'unverifiable')}).")
    return f"{who} called {v['tool']}, which is not in its approved manifest."


def _mock_provider() -> bool:
    return (get_setting("llm_provider") or "gateway").strip().lower() == "mock"


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
        with _lock:
            _inflight.discard(fid)
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
        # a call to a declared model dependency (the recon agent's break_classifier) that carries the
        # act.classifier.source marker is declared behaviour; value limits still apply
        declared = [r.name for r in conn.execute(select(db.dependencies.c.name).where(
            db.dependencies.c.agent_id == agent_id, db.dependencies.c.kind == "model")) if r.name]
        viol = find_violations(evs, man, set(declared))
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
        allowed_calls = allowed_declared_calls(evs, set(declared))
        if allowed_calls:
            detail["allowed_via_declared_dependency"] = allowed_calls
        need_llm = not _mock_provider()  # mock: the template stands, never passed off as model output
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
        with _lock:
            if fid in _inflight:
                return fid
            _inflight.add(fid)
        done = threading.Event()
        try:
            threading.Thread(target=_fill_explanation, daemon=True,
                             args=(fid, first, ag.name or agent_id, tier, fb, done)).start()
        except Exception:
            with _lock:
                _inflight.discard(fid)
            raise
        now = time.monotonic()
        if now - getattr(_batch, "last", -1e9) > BATCH_GAP_SECONDS:
            _batch.spent = 0.0  # new ingest request on this thread
        remaining = max(0.0, LLM_WAIT_SECONDS - getattr(_batch, "spent", 0.0))
        done.wait(remaining)
        _batch.last = time.monotonic()
        _batch.spent = getattr(_batch, "spent", 0.0) + (_batch.last - now)
    return fid
