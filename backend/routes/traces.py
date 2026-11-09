"""OTLP/HTTP trace ingest and audit-chain verification."""
import hashlib
import logging
import os
import zlib
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import func, insert, select, update

from .. import chain, db
from ..normalize import default_actor
from ..normalize_span import iso, native_agent_id, normalize_span
from ..otlp import parse_request

router = APIRouter()

# Hooks called with run_id after a run's new spans are committed (T-6 drift detector registers here).
run_ingested_hooks: list = []

log = logging.getLogger(__name__)
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


def register_run_hook(fn) -> None:
    """Register fn(run_id), called after commit for each run that gained new spans.

    A run may arrive in several batches, so a hook can fire more than once per run_id
    and must be idempotent. Exceptions are logged and never fail the ingest."""
    run_ingested_hooks.append(fn)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def shadow_id(native: str) -> str:
    return "act:shadow:" + hashlib.sha256(native.encode()).hexdigest()[:16]


def _max_bytes() -> int:
    try:
        return int(os.environ.get("ACT_OTLP_MAX_BYTES", DEFAULT_MAX_BYTES))
    except ValueError:
        return DEFAULT_MAX_BYTES


def _actor_from(headers) -> tuple:
    uid, name = headers.get("x-domino-user-id"), headers.get("x-domino-user")
    if uid or name:
        return (uid or name, name or uid)
    return default_actor()


def ingest(spans: list[dict], actor: tuple | None = None) -> dict:
    # Normalize before taking the lock.
    prepared = []
    for sp in spans:
        native, explicit = native_agent_id(sp["attributes"], sp["resource"])
        prepared.append((sp, native, explicit, normalize_span(sp, None, native)))

    matched_spans = 0
    runs: dict[str, dict] = {}
    items = []
    shadow: dict[str, dict] = {}
    conflicts: list[dict] = []
    with chain.lock, db.engine.begin() as conn:
        chain.begin_write(conn)
        amap = {r.native_id: r.id for r in conn.execute(select(db.agents.c.id, db.agents.c.native_id))}
        run_ids = {p[3]["run_id"] for p in prepared}
        seen = {(r.run_id, r.span_id) for r in conn.execute(
            select(db.events.c.run_id, db.events.c.span_id).where(
                db.events.c.run_id.in_(run_ids), db.events.c.span_id.is_not(None)))}
        # rows stored earlier: span tree links, the agent they carry and how it was resolved
        stored_rows = list(conn.execute(select(
            db.events.c.id, db.events.c.run_id, db.events.c.span_id, db.events.c.parent_span_id,
            db.events.c.agent_id, db.events.c.agent_source).where(db.events.c.run_id.in_(run_ids))))
        parent_of: dict[tuple, str | None] = {}
        expl: dict[tuple, str] = {}   # (run, span) -> explicit agent id

        def src_of(r):
            if r.agent_source:
                return r.agent_source
            return "unknown" if (not r.agent_id or r.agent_id == "unknown") else "explicit"

        for r in stored_rows:
            if r.span_id is not None:
                parent_of[(r.run_id, r.span_id)] = r.parent_span_id
                if src_of(r) == "explicit":
                    expl[(r.run_id, r.span_id)] = r.agent_id
        # only new spans are processed further (duplicates never become findings or events)
        new = []
        for sp, native, explicit, ev in prepared:
            key = (ev["run_id"], sp.get("span_id") or None)
            if key[1] is not None:
                if key in seen:
                    continue
                seen.add(key)
            own = amap.get(native) or (shadow_id(native) if native and explicit else None)
            new.append((sp, native, explicit, ev, key, own))
            if key[1] is not None:
                parent_of[key] = sp.get("parent_span_id") or None
                if own:
                    expl[key] = own

        def walk(rid, sid):
            """Nearest span from sid upward (inclusive) with an explicit agent; None if none."""
            hops = set()
            while sid and sid not in hops:
                hops.add(sid)
                if (rid, sid) in expl:
                    return expl[(rid, sid)]
                sid = parent_of.get((rid, sid))
            return None

        def is_root(rid, parent):
            return not parent or (rid, parent) not in parent_of

        # run agent: what is stored; else the earliest root span's agent; else explicit carrier,
        # then service.name match in this request
        stored: dict[str, str] = {}
        for r in conn.execute(select(db.runs.c.id, db.runs.c.agent_id).where(db.runs.c.id.in_(run_ids))):
            if r.agent_id and r.agent_id != "unknown":
                stored[r.id] = r.agent_id
        for r in conn.execute(select(db.events.c.run_id, db.events.c.agent_id, func.min(db.events.c.id).label("m"))
                              .where(db.events.c.run_id.in_(run_ids), db.events.c.agent_id.is_not(None),
                                     db.events.c.agent_id != "unknown")
                              .group_by(db.events.c.run_id, db.events.c.agent_id).order_by("m")):
            stored.setdefault(r.run_id, r.agent_id)
        run_agent: dict[str, str] = {}
        roots = sorted((x for x in new if is_root(x[3]["run_id"], x[0].get("parent_span_id") or None)),
                       key=lambda x: (x[3]["ts"], x[4][1] or ""))
        for sp, native, explicit, ev, key, own in roots:
            if own and ev["run_id"] not in run_agent:
                run_agent[ev["run_id"]] = own
        for tier in (True, False):
            for sp, native, explicit, ev, key, own in new:
                rid = ev["run_id"]
                if rid not in run_agent and native and explicit == tier and own:
                    run_agent[rid] = own
        final_agent = {rid: stored.get(rid) or run_agent.get(rid) for rid in run_ids}
        for sp, native, explicit, ev, key, own in roots:
            rid = ev["run_id"]
            if own and not sp.get("parent_span_id") and final_agent.get(rid) and own != final_agent[rid] and \
                    not any(c["rid"] == rid and c["claimed"] == own for c in conflicts):
                conflicts.append({"rid": rid, "claimed": own, "span_id": key[1], "kept": final_agent[rid]})
        run_agent = {rid: a for rid, a in final_agent.items() if a}
        for sp, native, explicit, ev, key, own in new:
            if native and explicit and not amap.get(native) and native not in shadow:
                shadow[native] = {"id": shadow_id(native), "ev": ev}
        for sp, native, explicit, ev, key, own in new:
            rid = ev["run_id"]
            if own:
                act_id, source = own, "explicit"
            else:
                anc = walk(rid, sp.get("parent_span_id") or None)
                if anc:
                    act_id, source = anc, "ancestor"
                elif run_agent.get(rid):
                    act_id, source = run_agent[rid], "run_fallback"
                else:
                    act_id, source = None, "unknown"
            if act_id and amap.get(native) == act_id:
                matched_spans += 1
            ev["agent_id"] = act_id or "unknown"
            items.append({"run_id": rid, "agent_id": ev["agent_id"], "agent_source": source, "ts": ev["ts"],
                          "kind": ev["kind"], "span_id": key[1], "raw_span": sp, "event": ev,
                          "parent_span_id": sp.get("parent_span_id") or None})
            r = runs.setdefault(rid, {"agent_id": run_agent.get(rid),
                                      "start": ev["ts"], "end": ev["ts"], "n": 0})
            r["start"] = min(r["start"], ev["ts"])
            end = sp.get("end_ns") and iso(sp["end_ns"])
            r["end"] = max(r["end"], end or ev["ts"])
            r["n"] += 1
        for native, sh in shadow.items():
            pid = sh["id"]
            if conn.execute(select(db.agents.c.id).where(db.agents.c.id == pid)).first() is None:
                conn.execute(insert(db.agents).values(
                    id=pid, source_platform="shadow", native_id=native, name=native,
                    status="shadow", last_seen=sh["ev"]["ts"]))
        # agents rows must exist before runs/events reference them
        # re-resolve stored rows still unknown / run_fallback; link columns only, canonical JSON,
        # hash and prev_hash stay untouched
        batches: dict[tuple, list] = {}
        for r in stored_rows:
            cur_src = src_of(r)
            if cur_src not in ("unknown", "run_fallback"):
                continue
            anc = walk(r.run_id, r.parent_span_id)
            if anc:
                tgt = (anc, "ancestor")
            elif cur_src == "unknown" and run_agent.get(r.run_id):
                tgt = (run_agent[r.run_id], "run_fallback")
            else:
                tgt = (r.agent_id, cur_src)
            if tgt != (r.agent_id, r.agent_source):
                batches.setdefault((r.run_id,) + tgt, []).append(r.id)
        for (rid, agent, source), ids in batches.items():
            for i in range(0, len(ids), 500):
                conn.execute(update(db.events).where(
                    db.events.c.run_id == rid, db.events.c.id.in_(ids[i:i + 500])
                ).values(agent_id=agent, agent_source=source))
        for rid in run_ids:
            if rid in run_agent and stored.get(rid) is None:
                conn.execute(update(db.runs).where(
                    db.runs.c.id == rid, (db.runs.c.agent_id.is_(None)) | (db.runs.c.agent_id == "unknown")
                ).values(agent_id=run_agent[rid]))
        chain.append_events(conn, items)
        for rid, r in runs.items():
            cur = conn.execute(select(db.runs).where(db.runs.c.id == rid)).first()
            if cur is None:
                conn.execute(insert(db.runs).values(
                    id=rid, agent_id=r["agent_id"], status="completed", started_at=r["start"],
                    ended_at=r["end"], record={"span_count": r["n"]}))
            else:
                rec = dict(cur.record or {})
                rec["span_count"] = rec.get("span_count", 0) + r["n"]
                conn.execute(update(db.runs).where(db.runs.c.id == rid).values(
                    agent_id=(cur.agent_id if cur.agent_id and cur.agent_id != "unknown" else r["agent_id"]),
                    started_at=min(cur.started_at or r["start"], r["start"]),
                    ended_at=max(cur.ended_at or r["end"], r["end"]), record=rec))
        caller = actor or default_actor()
        for c in conflicts:
            conn.execute(insert(db.audit_events).values(
                actor_user_id="system:ingest", actor_username="system:ingest", action="run_agent_conflict",
                target=c["rid"], ts=_now(),
                detail={"run_id": c["rid"], "stored_agent_id": c["kept"], "kept_agent_id": c["kept"],
                        "claimed_agent_id": c["claimed"], "span_id": c["span_id"],
                        "caller_user_id": caller[0], "caller_username": caller[1]}))
        for native, sh in shadow.items():
            pid, ev = sh["id"], sh["ev"]
            if conn.execute(select(db.findings.c.id).where(
                    db.findings.c.rule == "shadow_agent", db.findings.c.agent_id == pid)).first():
                continue
            conn.execute(insert(db.findings).values(
                agent_id=pid, run_id=ev["run_id"], severity="high", rule="shadow_agent",
                type="shadow_agent", message=f"Traces received from unregistered agent id {native}",
                status="open", created_at=_now(), span_id=ev["span_id"], ts=ev["ts"],
                detail={"native_agent_id": native, "run_id": ev["run_id"],
                        "explanation": "gen_ai.agent.id not found in agent inventory"}))
    for rid in runs:
        for fn in list(run_ingested_hooks):
            try:
                fn(rid)
            except Exception:  # hooks must not break ingest
                log.exception("run hook failed")
    return {"accepted": len(spans), "agents_matched": matched_spans}


async def _read_body(request: Request, limit: int) -> bytes:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        raise HTTPException(413, "request body too large")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, "request body too large")
    body = bytes(buf)
    enc = request.headers.get("content-encoding", "").strip().lower()
    if enc == "gzip":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            out = d.decompress(body, limit + 1)
            if d.unconsumed_tail:
                raise HTTPException(413, "request body too large")
            out += d.flush()
        except zlib.error as e:
            raise HTTPException(400, f"invalid gzip body: {e}")
        if len(out) > limit:
            raise HTTPException(413, "request body too large")
        return out
    if enc not in ("", "identity"):
        raise HTTPException(415, f"unsupported Content-Encoding {enc}")
    return body


@router.post("/v1/traces")
async def post_traces(request: Request):
    body = await _read_body(request, _max_bytes())
    try:
        spans = parse_request(body, request.headers.get("content-type", ""))
    except ValueError as e:
        return JSONResponse({"detail": f"invalid OTLP payload: {e}"}, status_code=400)
    try:
        return await run_in_threadpool(ingest, spans, _actor_from(request.headers))
    except (KeyError, TypeError, AttributeError) as e:
        return JSONResponse({"detail": f"invalid span: {e}"}, status_code=400)


@router.get("/api/audit/verify")
def audit_verify():
    return chain.verify()
