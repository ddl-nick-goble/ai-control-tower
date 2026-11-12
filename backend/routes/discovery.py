"""Scan and intake API (PO-4): start a scan, watch it, review staged rows, import, schedule syncs."""
import threading

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select

from .. import db, discovery
from ..connections import store
from ..domino_identity import User, require_user

router = APIRouter()
SCHEDULES = ("off", "daily")


def _connection(cid):
    with db.engine.connect() as cx:
        row = cx.execute(select(db.connections).where(db.connections.c.id == cid)).first()
    if row is None:
        raise HTTPException(404, "ACT has no connection with that id.")
    return row


def _scan(sid):
    s = discovery.get_scan(sid)
    if s is None:
        raise HTTPException(404, "ACT has no scan with that id.")
    return s


@router.post("/api/connections/{cid}/scans")
def start_scan(cid: str, user: User = Depends(require_user)):
    row = _connection(cid)
    if row.state not in store.TRUSTED_OR_LATER:
        raise HTTPException(409, "ACT hasn't seen the trust yet. Finish the setup step before scanning.")
    try:
        return discovery.start_scan(cid, user)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ACT could not start the scan: {e}")


@router.get("/api/connections/{cid}/scans")
def list_scans(cid: str, _user: User = Depends(require_user)):
    _connection(cid)
    with db.engine.connect() as cx:
        ids = [r.id for r in cx.execute(select(db.scans.c.id).where(db.scans.c.connection_id == cid)
                                        .order_by(db.scans.c.started_at.desc()).limit(20))]
    return {"scans": [discovery.get_scan(i) for i in ids]}


@router.get("/api/scans/{sid}")
def get_scan(sid: str, _user: User = Depends(require_user)):
    return _scan(sid)


@router.get("/api/scans/{sid}/staged")
def staged(sid: str, cls: str = Query("", alias="class"), _user: User = Depends(require_user)):
    _scan(sid)
    q = select(db.staged_agents).where(db.staged_agents.c.scan_id == sid)
    if cls:
        if cls not in discovery.CLASSES:
            raise HTTPException(422, "class must be new, changed, unchanged or missing.")
        q = q.where(db.staged_agents.c["class"] == cls)
    with db.engine.connect() as cx:
        rows = cx.execute(q.order_by(db.staged_agents.c.id)).all()
    return {"rows": discovery.with_current_tier(rows)}


@router.patch("/api/staged/{rid}")
def patch_staged(rid: int, body: dict = Body(...), user: User = Depends(require_user)):
    overrides = body.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise HTTPException(422, "overrides must be an object")
    try:
        row = discovery.update_staged(rid, body.get("owner"), overrides, user)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if row is None:
        raise HTTPException(404, "ACT has no staged agent with that id.")
    return row


@router.post("/api/staged/{rid}/dismiss")
def dismiss_staged(rid: int, body: dict = Body(...), user: User = Depends(require_user)):
    reason = str(body.get("reason") or "").strip()
    if len(reason) < 10:
        raise HTTPException(422, "Enter a reason of at least 10 characters.")
    row = discovery.dismiss_staged(rid, reason, user)
    if row is None:
        raise HTTPException(404, "ACT has no staged agent with that id.")
    return row


@router.post("/api/scans/{sid}/import")
def import_scan(sid: str, body: dict = Body(...), user: User = Depends(require_user)):
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(422, 'Send {"ids": [staged row ids]}.')
    _scan(sid)
    return discovery.import_rows(sid, ids, user)


@router.put("/api/connections/{cid}/schedule")
def put_schedule(cid: str, body: dict = Body(...), _user: User = Depends(require_user)):
    row = _connection(cid)
    schedule = body.get("schedule")
    at = str(body.get("time") or row.schedule_time or "02:00")
    if schedule not in SCHEDULES:
        raise HTTPException(422, "schedule must be off or daily.")
    try:
        hh, mm = (int(x) for x in at.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except (ValueError, AssertionError):
        raise HTTPException(422, "time must be HH:MM, 24-hour.")
    job_id = row.schedule_job_id
    if not row.sample:  # a sample connection only stores the choice; there is nothing to run on a schedule
        try:
            res = discovery.schedule_job(cid, schedule, time=f"{hh:02d}:{mm:02d}", job_id=job_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"ACT could not change the scheduled Domino Job: {e}")
        job_id = (res or {}).get("job_id") if isinstance(res, dict) else None
    with db.engine.begin() as cx:
        cx.execute(db.connections.update().where(db.connections.c.id == cid).values(
            schedule=schedule, schedule_time=f"{hh:02d}:{mm:02d}", schedule_job_id=job_id))
    return {"schedule": schedule, "time": f"{hh:02d}:{mm:02d}", "job_id": job_id, "sample": bool(row.sample)}


@router.get("/api/connections/{cid}/schedule")
def get_schedule(cid: str, _user: User = Depends(require_user)):
    row = _connection(cid)
    return {"schedule": row.schedule or "off", "time": row.schedule_time or "02:00", "job_id": row.schedule_job_id,
            "saved": bool(row.schedule), "sample": bool(row.sample)}


@router.post("/api/connections/{cid}/sync")
def sync_now(cid: str, _user: User = Depends(require_user)):
    """Run a sync now (what the schedule does): stages new agents as Awaiting import, raises findings."""
    sample = bool(_connection(cid).sample)
    if sample or discovery.inline():  # a sample sync is in-process and quick: it finishes before the answer, never listed as running
        return {"scan_id": discovery.run_sync(cid)}
    sid = discovery.create_scan(cid, None, "sync", sample)
    threading.Thread(target=discovery.run_scan, args=(sid, db.engine), daemon=True).start()
    return {"scan_id": sid}


@router.get("/api/connections/{cid}/awaiting")
def awaiting(cid: str, _user: User = Depends(require_user)):
    _connection(cid)
    with db.engine.connect() as cx:
        rows = cx.execute(select(db.staged_agents).where(
            db.staged_agents.c.connection_id == cid, db.staged_agents.c.awaiting.is_(True),
            db.staged_agents.c.imported_agent_id.is_(None)).order_by(db.staged_agents.c.id)).all()
        scan_id = rows[-1].scan_id if rows else None
    return {"rows": discovery.with_current_tier(rows), "scan_id": scan_id, "count": len(rows)}
