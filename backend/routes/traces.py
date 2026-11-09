"""OTLP/HTTP trace ingest and audit-chain verification."""
import hashlib
import logging
import os
import zlib
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import insert, select, update

from .. import chain, db
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


def ingest(spans: list[dict]) -> dict:
    # Normalize before taking the lock.
    prepared = []
    for sp in spans:
        native, explicit = native_agent_id(sp["attributes"], sp["resource"])
        prepared.append((sp, native, explicit, normalize_span(sp, None, native)))

    matched_spans = 0
    runs: dict[str, dict] = {}
    items = []
    shadow: dict[str, dict] = {}
    with chain.lock, db.engine.begin() as conn:
        chain.begin_write(conn)
        amap = {r.native_id: r.id for r in conn.execute(select(db.agents.c.id, db.agents.c.native_id))}
        seen = {(r.run_id, r.span_id) for r in conn.execute(
            select(db.events.c.run_id, db.events.c.span_id).where(
                db.events.c.run_id.in_({p[3]["run_id"] for p in prepared}),
                db.events.c.span_id.is_not(None)))}
        # Resolve each run's agent: explicit carrier span, then service.name match in this
        # request, then what is already stored for the run.
        run_ids = {p[3]["run_id"] for p in prepared}
        run_agent: dict[str, str] = {}
        for tier in (True, False):
            for sp, native, explicit, ev in prepared:
                rid = ev["run_id"]
                if rid in run_agent or not native or explicit != tier:
                    continue
                act_id = amap.get(native)
                if act_id:
                    run_agent[rid] = act_id
                elif explicit:
                    run_agent[rid] = shadow_id(native)
        for sp, native, explicit, ev in prepared:
            # a span carrying its own unmatched agent id keeps its shadow placeholder
            if native and explicit and not amap.get(native) and native not in shadow:
                shadow[native] = {"id": shadow_id(native), "ev": ev}
        stored: dict[str, str] = {}
        for r in conn.execute(select(db.runs.c.id, db.runs.c.agent_id).where(db.runs.c.id.in_(run_ids))):
            if r.agent_id and r.agent_id != "unknown":
                stored[r.id] = r.agent_id
        for r in conn.execute(select(db.events.c.run_id, db.events.c.agent_id).where(
                db.events.c.run_id.in_(run_ids), db.events.c.agent_id.is_not(None),
                db.events.c.agent_id != "unknown").distinct()):
            stored.setdefault(r.run_id, r.agent_id)
        to_relink = [rid for rid in run_ids if rid in run_agent and stored.get(rid) is None]
        for rid in run_ids:
            if rid not in run_agent and rid in stored:
                run_agent[rid] = stored[rid]
        for sp, native, explicit, ev in prepared:
            key = (ev["run_id"], sp.get("span_id") or None)
            if key[1] is not None:
                if key in seen:
                    continue
                seen.add(key)
            act_id = amap.get(native) or (shadow_id(native) if native and explicit else None) \
                or run_agent.get(ev["run_id"])
            if act_id and amap.get(native) == act_id:
                matched_spans += 1
            ev["agent_id"] = act_id or "unknown"
            items.append({"run_id": ev["run_id"], "agent_id": ev["agent_id"], "ts": ev["ts"],
                          "kind": ev["kind"], "span_id": key[1], "raw_span": sp, "event": ev})
            r = runs.setdefault(ev["run_id"], {"agent_id": act_id, "start": ev["ts"], "end": ev["ts"], "n": 0})
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
        if to_relink:
            # link column only; canonical JSON, hash and prev_hash stay untouched
            for rid in to_relink:
                conn.execute(update(db.events).where(
                    db.events.c.run_id == rid,
                    (db.events.c.agent_id == "unknown") | db.events.c.agent_id.is_(None)
                ).values(agent_id=run_agent[rid]))
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
        return await run_in_threadpool(ingest, spans)
    except (KeyError, TypeError, AttributeError) as e:
        return JSONResponse({"detail": f"invalid span: {e}"}, status_code=400)


@router.get("/api/audit/verify")
def audit_verify():
    return chain.verify()
