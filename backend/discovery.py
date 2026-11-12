"""Scan and intake (PO-4): run a discovery scan, stage what it found, import the selection, sync on a schedule.

A LIVE scan runs as a Domino Job (jobs/discover.py -> run_scan). A SAMPLE scan runs in a background thread; with
ACT_SCAN_INLINE=1 every scan runs in the calling thread (tests). Progress is written to the scans table, so a closed
browser does not stop anything and a reload picks the scan up again.
"""
import copy
import logging
import os
import secrets
import threading
import time

from sqlalchemy import delete, insert, select, update

from . import db, governance
from .connections import store
from .normalize import _audit, act_id, apply_record, normalize, now_iso
from .suggest import suggest_scores
from .tiering import load_config, score_tier

log = logging.getLogger(__name__)
CLASSES = ("new", "changed", "unchanged", "missing")


# ------------------------------------------------------------------ Domino Jobs API (monkeypatched in tests)
def _job_command(connection_id, scan_id=None):
    if scan_id:
        return f"python jobs/discover.py --connection {connection_id} --scan {scan_id}"
    return f"python jobs/discover.py --connection {connection_id} --sync"


def launch_job(connection_id, scan_id, **kw):
    """Start the discovery Job through the Domino Jobs API (no auth header; the proxy adds it).
    -> {"job_id", "job_url"}."""
    import requests
    base = os.environ["DOMINO_API_PROXY"].rstrip("/")
    body = {"projectId": os.environ["DOMINO_PROJECT_ID"], "runCommand": _job_command(connection_id, scan_id),
            "title": f"ACT discovery scan {scan_id}"}
    if os.environ.get("ACT_DISCOVER_HARDWARE_TIER"):
        body["hardwareTier"] = os.environ["ACT_DISCOVER_HARDWARE_TIER"]
    r = requests.post(f"{base}/api/jobs/v1/jobs", json=body, timeout=30)
    r.raise_for_status()
    job_id = r.json()["job"]["id"]
    host = (os.environ.get("DOMINO_USER_HOST") or "").rstrip("/")
    owner, project = os.environ.get("DOMINO_PROJECT_OWNER", ""), os.environ.get("DOMINO_PROJECT_NAME", "")
    return {"job_id": job_id, "job_url": f"{host}/jobs/{owner}/{project}/{job_id}"}


def schedule_job(connection_id, schedule, time="02:00", job_id=None, **kw):
    """Create or remove the scheduled Domino Job that runs the sync. -> {"job_id": id or None}.
    Uses the v4 scheduled-jobs route (non-public; the Jobs skill documents the body)."""
    import requests
    base = os.environ["DOMINO_API_PROXY"].rstrip("/")
    project = os.environ["DOMINO_PROJECT_ID"]
    if job_id:  # replace or remove the old schedule
        requests.delete(f"{base}/v4/projects/{project}/scheduledjobs/{job_id}", timeout=30)
    if schedule == "off":
        return {"job_id": None}
    hh, mm = (int(x) for x in time.split(":"))
    me = requests.get(f"{base}/api/users/v1/self", timeout=30).json()["user"]["id"]
    body = {"title": f"ACT sync {connection_id}", "command": _job_command(connection_id).replace("python ", "", 1),
            "schedule": {"cronString": f"0 {mm} {hh} * * ?", "isCustom": True}, "timezoneId": "UTC",
            "isPaused": False, "allowConcurrentExecution": False, "scheduledByUserId": me,
            "hardwareTierIdentifier": os.environ.get("ACT_DISCOVER_HARDWARE_TIER", "small-k8s"),
            "environmentRevisionSpec": "ActiveRevision", "notifyOnCompleteEmailAddresses": []}
    r = requests.post(f"{base}/v4/projects/{project}/scheduledjobs", json=body, timeout=30)
    r.raise_for_status()
    return {"job_id": r.json()["id"]}


# ------------------------------------------------------------------ scan lifecycle
def inline() -> bool:
    return os.environ.get("ACT_SCAN_INLINE") == "1"


def create_scan(cid, user=None, kind="scan", sample=False, eng=None) -> str:
    sid = "scan-" + secrets.token_hex(6)
    ts = now_iso()
    uid, uname = (user.user_id, user.username) if user else ("system:sync", "sync")
    with (eng or db.engine).begin() as cx:
        cx.execute(insert(db.scans).values(
            id=sid, connection_id=cid, kind=kind, state="queued", sample=sample, started_at=ts, updated_at=ts,
            progress=[], counts={"found": 0, "new": 0, "changed": 0, "unchanged": 0, "missing": 0},
            created_by=uid, created_by_username=uname))
    return sid


def set_scan(sid, eng=None, **vals):
    with (eng or db.engine).begin() as cx:
        cx.execute(update(db.scans).where(db.scans.c.id == sid).values(updated_at=now_iso(), **vals))


def start_scan(cid, user):
    """Create a scan for a connection and launch it. -> the POST /scans answer."""
    with db.engine.connect() as cx:
        row = cx.execute(select(db.connections).where(db.connections.c.id == cid)).first()
    sample = bool(row.sample)
    sid = create_scan(cid, user, "scan", sample)
    if inline():
        run_scan(sid)
        return {"scan_id": sid, "sample": sample, "job_id": None, "job_url": None}
    if sample:
        eng = db.engine
        threading.Thread(target=run_scan, args=(sid, eng), daemon=True).start()
        return {"scan_id": sid, "sample": True, "job_id": None, "job_url": None}
    try:
        job = launch_job(cid, sid)
    except Exception as e:  # noqa: BLE001 - shown to the user
        set_scan(sid, state="failed", error=f"Could not start the Domino Job: {e}"[:300])
        raise
    set_scan(sid, state="running", job_id=job["job_id"], job_url=job["job_url"])
    return {"scan_id": sid, "sample": False, "job_id": job["job_id"], "job_url": job["job_url"]}


def run_sync(connection_id):
    """The scheduled-sync entry point: run a scan, never import. -> the scan id."""
    with db.engine.connect() as cx:
        row = cx.execute(select(db.connections).where(db.connections.c.id == connection_id)).first()
    sid = create_scan(connection_id, None, "sync", bool(row and row.sample))
    run_scan(sid)
    return sid


def _leaves(tree):
    for n in tree:
        kids = n.get("children") or []
        if kids:
            yield from _leaves(kids)
        elif n.get("selected", True):
            yield n["id"]


def run_scan(sid, eng=None):
    """Discover each selected scope (one failing scope does not stop the others), classify, stage."""
    eng = eng or db.engine
    try:
        _run_scan(sid, eng)
    except Exception as e:  # noqa: BLE001
        log.exception("scan %s failed", sid)
        set_scan(sid, eng, state="failed", error=str(e)[:300], finished_at=now_iso())


def _run_scan(sid, eng):
    with eng.connect() as cx:
        scan = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
        conn = cx.execute(select(db.connections).where(db.connections.c.id == scan.connection_id)).first()
    set_scan(sid, eng, state="running")
    from adapters import get_adapter
    adapter = get_adapter(conn.provider)
    if adapter is None:
        raise RuntimeError(f"No adapter for {conn.provider}.")
    cfg = dict(conn.config or {})
    creds = None
    if conn.sample:
        cfg["sample"] = True
    else:
        from .connections import federation
        for k, v in (("aws", ("role_arn", cfg.get("aws_role_arn") or cfg.get("role"))),):
            if conn.provider == k and not cfg.get(v[0]):
                cfg[v[0]] = v[1]
        creds = federation.exchange(conn.provider, cfg)
    # The sample tenant sits on top of the demo Fleet fixtures; those agents only show when the fleet already has them
    # (as Unchanged), never as New.
    fleet = set()
    if conn.sample and hasattr(adapter, "list_agents_replay"):
        fleet = {a.native_id for a in adapter.list_agents_replay()}
    scopes = list(_leaves(store.scope_tree(conn._mapping))) or [None]
    progress = [{"scope": s or "all", "found": 0, "errors": [], "state": "queued"} for s in scopes]
    set_scan(sid, eng, progress=copy.deepcopy(progress))
    found = {}  # native_id -> SourceAgent
    for p, scope in zip(progress, scopes):
        p["state"] = "running"
        set_scan(sid, eng, progress=copy.deepcopy(progress))
        last = [time.time()]

        def tick(done=0, total=0, message="", p=p):
            if time.time() - last[0] >= 2:  # keep the heartbeat under 5 s on long scopes
                last[0] = time.time()
                p["message"] = str(message)[:100]
                set_scan(sid, eng, progress=copy.deepcopy(progress))
        try:
            agents = adapter.discover(cfg, creds, [scope] if scope else [], progress=tick)
            p["found"] = len(agents)
            for sa in agents:
                found.setdefault(sa.native_id, sa)
            p["state"] = "done"
        except Exception as e:  # noqa: BLE001
            p["errors"].append(str(e)[:300])
            p["state"] = "failed"
        set_scan(sid, eng, progress=copy.deepcopy(progress))
    if fleet:
        with eng.connect() as cx:
            have = {r.native_id for r in cx.execute(select(db.agents.c.native_id))}
        found = {k: v for k, v in found.items() if k not in fleet or k in have}
    failed = sum(1 for p in progress if p["errors"])
    counts = stage(sid, scan.connection_id, found, bool(conn.sample), scan.kind == "sync", complete=not failed, eng=eng)
    state = "failed" if failed == len(progress) else "partial" if failed else "done"
    ts = now_iso()
    with eng.begin() as cx:
        if state != "failed":
            vals = {"last_scan": ts}
            if conn.state in ("verified", "partial"):
                vals["state"] = "connected"
            cx.execute(update(db.connections).where(db.connections.c.id == conn.id).values(**vals))
    set_scan(sid, eng, state=state, counts=counts, finished_at=ts)


# ------------------------------------------------------------------ classify and stage
def _facts(rec) -> dict:
    man = rec.get("manifest") or {}
    deps = rec.get("dependencies") or {}
    return {"name": rec.get("name"), "description": rec.get("description"), "version": rec.get("version"),
            "tools": man.get("tools"), "data_scopes": man.get("data_scopes"), "systems": man.get("systems"),
            "value_limits": man.get("value_limits"), "identity": rec.get("identity"),
            "models": sorted(str(d.get("name")) for d in deps.get("models", [])),
            "mcp_servers": sorted(str(d.get("name")) for d in deps.get("mcp_servers", []))}


def field_diff(old_rec, new_rec) -> dict:
    a, b = _facts(old_rec), _facts(new_rec)
    return {k: {"before": a[k], "after": b[k]} for k in b if a[k] != b[k]}


def _flags(rec, tier) -> list:
    out = []
    if tier == 1:
        out.append("tier_1")
    if not (rec.get("identity") or {}).get("distinct"):
        out.append("no_distinct_identity")
    if any(str(s).lower().endswith(":write") for s in (rec.get("manifest") or {}).get("data_scopes") or []):
        out.append("write_scopes")
    if not rec.get("owner") or rec.get("owner") == "unassigned":
        out.append("no_owner")
    return out


def _suggestion(rec) -> dict:
    sug = suggest_scores(rec)
    return {"suggestions": sug, "tier": score_tier({k: v["score"] for k, v in sug.items()}).tier}


def stage(sid, cid, found, sample, is_sync, complete=True, eng=None) -> dict:
    """Class every found agent against the agents table and store the rows. -> counts."""
    eng = eng or db.engine
    counts = {"found": len(found), "new": 0, "changed": 0, "unchanged": 0, "missing": 0}
    changes, gone = [], []
    with eng.begin() as cx:
        for sa in found.values():
            rec = normalize(sa)
            old = cx.execute(select(db.agents).where(db.agents.c.id == rec["id"])).first()
            diff, cls = None, "new"
            if old is not None:
                diff = field_diff(old.record or {}, rec)
                cls = "changed" if diff else "unchanged"
                rec["owner"] = old.owner
            counts[cls] += 1
            sug = _suggestion(rec)
            if is_sync and cls == "new":  # a re-sync replaces the older awaiting row
                cx.execute(delete(db.staged_agents).where(
                    db.staged_agents.c.connection_id == cid, db.staged_agents.c.native_id == sa.native_id,
                    db.staged_agents.c.awaiting.is_(True), db.staged_agents.c.imported_agent_id.is_(None)))
            cx.execute(insert(db.staged_agents).values(
                scan_id=sid, connection_id=cid, platform=sa.platform, native_id=sa.native_id, name=sa.name,
                **{"class": cls}, diff=diff, suggestion=sug, overrides={},
                owner=None if rec["owner"] in (None, "unassigned") else rec["owner"], flags=_flags(rec, sug["tier"]),
                sample=sample, awaiting=bool(is_sync and cls == "new"), record=rec))
            if cls == "changed":
                changes.append((rec["id"], sa.name, diff))
        if complete:  # a failed scope would make healthy agents look missing
            for a in cx.execute(select(db.agents).where(db.agents.c.connection_id == cid)).all():
                if a.native_id not in found:
                    counts["missing"] += 1
                    cx.execute(insert(db.staged_agents).values(
                        scan_id=sid, connection_id=cid, platform=a.source_platform, native_id=a.native_id,
                        name=a.name, **{"class": "missing"}, owner=a.owner, sample=sample, imported_agent_id=a.id,
                        flags=[], overrides={}))
                    gone.append((a.id, a.name))
        if is_sync:
            for aid, name, diff in changes:
                _finding(cx, aid, "source_changed", f"{name} changed at the source: {', '.join(diff)}.", diff)
            for aid, name in gone:
                _finding(cx, aid, "source_missing", f"{name} is no longer found at the source.", {})
    return counts


def _finding(cx, agent_id, kind, message, detail):
    open_same = cx.execute(select(db.findings.c.id, db.findings.c.detail).where(
        db.findings.c.agent_id == agent_id, db.findings.c.type == kind, db.findings.c.status == "open")).all()
    if any((f.detail or {}) == detail for f in open_same):
        return
    ts = now_iso()
    cx.execute(insert(db.findings).values(agent_id=agent_id, severity="medium", rule=kind, type=kind, message=message,
                                          status="open", created_at=ts, ts=ts, detail=detail))


# ------------------------------------------------------------------ staged rows: view, edit, import
def effective(row) -> dict:
    """The stored PO-2 suggestion with the user's overrides applied; the tier is recomputed. An override keyed "tier"
    ({score: 1-4, reason}) sets the tier itself (the Change tier control); suggested_tier is what ACT proposed."""
    sug = copy.deepcopy(row.suggestion or {})
    if not sug:
        return {}
    ovr = dict(row.overrides or {})
    tier_ovr = ovr.pop("tier", None)
    for crit, o in ovr.items():
        before = sug["suggestions"][crit]["score"]
        sug["suggestions"][crit] = {"score": o["score"], "evidence": "override",
                                    "reason": f"Override: {o['reason']} (suggested {before})"}
    r = score_tier({k: v["score"] for k, v in sug["suggestions"].items()})
    base_tier = score_tier({k: v["score"] for k, v in (row.suggestion or {})["suggestions"].items()}).tier
    tier, reasoning = r.tier, r.reasoning
    if tier_ovr:
        tier = int(tier_ovr["score"])
        reasoning = f"Tier {tier} set by the reviewer: {tier_ovr['reason']} ACT computed {r.reasoning}"
    return {"suggestions": sug["suggestions"], "tier": tier, "weighted": r.score, "reasoning": reasoning,
            "suggested_tier": base_tier}


def row_view(r) -> dict:
    rec = r.record or {}
    man = rec.get("manifest") or {}
    return {"id": r.id, "scan_id": r.scan_id, "class": r._mapping["class"], "platform": r.platform, "native_id": r.native_id,
            "name": r.name, "diff": r.diff, "suggestion": effective(r), "overrides": r.overrides or {},
            "owner": r.owner, "flags": r.flags or [], "sample": bool(r.sample), "awaiting": bool(r.awaiting),
            "imported_agent_id": r.imported_agent_id,
            "summary": {"tools": man.get("tools", []), "data_scopes": man.get("data_scopes", []),
                        "identity": rec.get("identity"), "description": rec.get("description")}}


def with_current_tier(rows) -> list:
    """Row views for staged rows, plus current_tier (the tier in ACT today) for Changed and Unchanged rows."""
    views = [row_view(r) for r in rows]
    ids = {r.id: (r.record or {}).get("id") for r in rows if r._mapping["class"] in ("changed", "unchanged")}
    if ids:
        with db.engine.connect() as cx:
            tiers = {a.id: a.tier for a in cx.execute(select(db.agents.c.id, db.agents.c.tier).where(
                db.agents.c.id.in_([i for i in ids.values() if i])))}
        for v in views:
            t = tiers.get(ids.get(v["id"]))
            if t is not None and str(t).isdigit():
                v["current_tier"] = int(t)
    return views


def dismiss_staged(rid, reason, user):
    """Take an awaiting row out of the queue. -> row view, None when missing. The reason is audited."""
    with db.engine.begin() as cx:
        r = cx.execute(select(db.staged_agents).where(db.staged_agents.c.id == rid)).first()
        if r is None:
            return None
        cx.execute(update(db.staged_agents).where(db.staged_agents.c.id == rid).values(awaiting=False))
        _audit(cx, "staged_dismissed", act_id(r.platform, r.native_id),
               {"staged_id": rid, "scan_id": r.scan_id, "connection_id": r.connection_id, "reason": reason},
               actor=(user.user_id, user.username))
        return row_view(cx.execute(select(db.staged_agents).where(db.staged_agents.c.id == rid)).first())


def _stale(updated_at, secs=300) -> bool:
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(updated_at))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - t).total_seconds() > secs


def get_scan(sid):
    with db.engine.connect() as cx:
        s = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
    if s is None:
        return None
    if s.state in ("queued", "running") and _stale(s.updated_at):
        set_scan(sid, state="failed", error="Scan stopped unexpectedly", finished_at=now_iso())
        with db.engine.connect() as cx:
            s = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
    return {"scan_id": s.id, "connection_id": s.connection_id, "kind": s.kind, "state": s.state, "sample": bool(s.sample),
            "job_id": s.job_id, "job_url": s.job_url, "progress": s.progress or [], "counts": s.counts or {},
            "error": s.error, "started_at": s.started_at, "finished_at": s.finished_at, "updated_at": s.updated_at}


def update_staged(rid, owner, overrides, user):
    """-> row view, None when missing. Raises ValueError (message for a 422) on bad input."""
    crit_names = set(load_config()["criteria"])
    for crit, o in (overrides or {}).items():
        if crit not in crit_names and crit != "tier":
            raise ValueError(f"Unknown criterion: {crit}")
        sc = (o or {}).get("score")
        top = 4 if crit == "tier" else 5
        if isinstance(sc, bool) or not isinstance(sc, int) or not 1 <= sc <= top:
            raise ValueError(f"The score for {crit} must be a whole number from 1 to {top}.")
        if not str((o or {}).get("reason") or "").strip():
            raise ValueError(f"An override needs a reason ({crit}).")
    with db.engine.begin() as cx:
        r = cx.execute(select(db.staged_agents).where(db.staged_agents.c.id == rid)).first()
        if r is None:
            return None
        vals = {}
        if owner is not None:
            vals["owner"] = str(owner).strip() or None
        if overrides:
            merged = dict(r.overrides or {})
            for crit, o in overrides.items():
                before = ((r.suggestion or {}).get("suggestions") or {}).get(crit, {}).get("score")
                if crit == "tier":
                    before = effective(r).get("suggested_tier")
                merged[crit] = {"score": o["score"], "reason": o["reason"].strip()}
                _audit(cx, "tier_override" if crit == "tier" else "criteria_override", act_id(r.platform, r.native_id),
                       {"agent_ref": act_id(r.platform, r.native_id), "criterion": crit, "from": before,
                        "to": o["score"], "reason": o["reason"].strip(), "staged_id": rid},
                       actor=(user.user_id, user.username))
            vals["overrides"] = merged
        if vals:
            cx.execute(update(db.staged_agents).where(db.staged_agents.c.id == rid).values(**vals))
        return row_view(cx.execute(select(db.staged_agents).where(db.staged_agents.c.id == rid)).first())


def import_rows(sid, ids, user) -> dict:
    """Import New rows as Pending review agents and apply Changed rows. Rows may come from the scan or from this
    connection's awaiting list. -> {imported, updated, no_owner, missing, skipped, agent_ids}."""
    out = {"imported": 0, "updated": 0, "no_owner": 0, "missing": 0, "skipped": 0, "agent_ids": []}
    actor = (user.user_id, user.username)
    with db.engine.connect() as cx:
        scan = cx.execute(select(db.scans).where(db.scans.c.id == sid)).first()
        rows = cx.execute(select(db.staged_agents).where(
            db.staged_agents.c.id.in_(list(ids)), db.staged_agents.c.connection_id == scan.connection_id)).all()
    out["skipped"] += len(set(ids)) - len(rows)  # unknown ids or rows of another connection
    for r in rows:
        if r.imported_agent_id or r._mapping["class"] == "unchanged":
            out["skipped"] += 1
        elif r._mapping["class"] == "missing":
            out["missing"] += 1
        else:
            no_owner = r._mapping["class"] == "new" and not r.owner
            aid, is_new = _import_one(r, scan, user, actor, owner=r.owner or "unassigned")
            if no_owner:
                out["no_owner"] += 1
            out["imported" if is_new else "updated"] += 1
            out["agent_ids"].append(aid)
    return out


def _import_one(r, scan, user, actor, owner=None):
    eff = effective(r)
    scores = {k: v["score"] for k, v in eff["suggestions"].items()}
    rec = dict(r.record)
    rec.update(owner=owner or r.owner or rec.get("owner"), sample=bool(r.sample), status="pending_review")
    with db.engine.begin() as cx:
        old = cx.execute(select(db.agents).where(db.agents.c.id == rec["id"])).first()
        apply_record(cx, rec, actor, connection_id=scan.connection_id)
        cur = cx.execute(select(db.agents).where(db.agents.c.id == rec["id"])).first()
        tier = str(eff["tier"])
        status = "pending_review" if old is None or int(tier) < int(old.tier) else old.status
        record = {**(cur.record or {}), "tier": eff["tier"], "tier_reasoning": eff["reasoning"], "criteria": scores,
                  "status": status, "sample": bool(r.sample)}
        cx.execute(update(db.agents).where(db.agents.c.id == rec["id"]).values(
            tier=tier, tier_reasoning=eff["reasoning"], criteria=scores, status=status, owner=rec["owner"],
            record=record, connection_id=scan.connection_id))
        _audit(cx, "imported" if old is None else "reimported", rec["id"],
               {"user": user.username, "connection_id": scan.connection_id, "scan_id": r.scan_id, "tier": tier,
                "from_tier": None if old is None else old.tier, "status": status}, actor)
        cx.execute(update(db.staged_agents).where(db.staged_agents.c.id == r.id).values(imported_agent_id=rec["id"]))
    try:
        governance.ensure_bundle(rec["id"])
    except Exception:  # noqa: BLE001 - the agent is imported; the bundle can be retried
        log.exception("ensure_bundle failed for %s", rec["id"])
    return rec["id"], old is None
