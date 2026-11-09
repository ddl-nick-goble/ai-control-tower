"""OTLP/HTTP trace ingest and audit-chain verification."""
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import insert, select, update

from .. import chain, db
from ..normalize_span import iso, native_agent_id, normalize_span
from ..otlp import parse_request

router = APIRouter()

# Hooks called with run_id after a run's spans are ingested (T-6 drift detector registers here).
run_ingested_hooks: list = []


def register_run_hook(fn) -> None:
    run_ingested_hooks.append(fn)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ingest(spans: list[dict]) -> dict:
    matched_spans = 0
    runs: dict[str, dict] = {}
    items = []
    shadow: dict[str, dict] = {}
    with chain.lock, db.engine.begin() as conn:
        amap = {r.native_id: r.id for r in conn.execute(select(db.agents.c.id, db.agents.c.native_id))}
        for sp in spans:
            native, explicit = native_agent_id(sp["attributes"], sp["resource"])
            act_id = amap.get(native) if native else None
            if act_id:
                matched_spans += 1
            elif native and explicit and native not in shadow:
                shadow[native] = sp
            ev = normalize_span(sp, act_id, native)
            items.append({"run_id": ev["run_id"], "agent_id": ev["agent_id"], "ts": ev["ts"],
                          "kind": ev["kind"], "raw_span": sp, "event": ev})
            r = runs.setdefault(ev["run_id"], {"agent_id": act_id, "start": ev["ts"], "end": ev["ts"], "n": 0})
            r["agent_id"] = r["agent_id"] or act_id
            r["start"] = min(r["start"], ev["ts"])
            end = sp.get("end_ns") and iso(sp["end_ns"])
            r["end"] = max(r["end"], end or ev["ts"])
            r["n"] += 1
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
                    agent_id=cur.agent_id or r["agent_id"],
                    started_at=min(cur.started_at or r["start"], r["start"]),
                    ended_at=max(cur.ended_at or r["end"], r["end"]), record=rec))
        for native, sp in shadow.items():
            exists = [f for f in conn.execute(select(db.findings.c.detail).where(
                db.findings.c.rule == "shadow_agent", db.findings.c.agent_id.is_(None)))
                if (f.detail or {}).get("native_agent_id") == native]
            if exists:
                continue
            conn.execute(insert(db.findings).values(
                agent_id=None, run_id=sp["trace_id"], severity="high", rule="shadow_agent",
                type="shadow_agent", message=f"Traces received from unregistered agent id {native}",
                status="open", created_at=_now(), span_id=sp["span_id"],
                ts=items[0]["ts"], reason="gen_ai.agent.id not found in agent inventory",
                observed_value=native, detail={"native_agent_id": native, "run_id": sp["trace_id"]}))
    for rid in runs:
        for fn in list(run_ingested_hooks):
            try:
                fn(rid)
            except Exception:  # hooks must not break ingest
                import logging
                logging.getLogger(__name__).exception("run hook failed")
    return {"accepted": len(spans), "agents_matched": matched_spans}


@router.post("/v1/traces")
async def post_traces(request: Request):
    body = await request.body()
    try:
        spans = parse_request(body, request.headers.get("content-type", ""))
    except Exception as e:
        return JSONResponse({"detail": f"invalid OTLP payload: {e}"}, status_code=400)
    return await run_in_threadpool(ingest, spans)


@router.get("/api/audit/verify")
def audit_verify():
    return chain.verify()
