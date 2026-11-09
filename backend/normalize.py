"""SourceAgent -> Agent Record (A2A Agent Card fields, OASF tags, bank fields); tier inference; upsert."""
import hashlib
import os
from datetime import datetime, timezone

from sqlalchemy import delete, insert, select, update

from . import db
from .tiering import score_tier

CRITERIA = ("decision_impact", "financial_exposure", "customer_impact", "autonomy",
            "data_sensitivity", "reversibility", "regulatory_exposure")
SENSITIVE_DATA = (("pii", "kyc", "sanction"), ("customer", "payment", "wealth", "credit", "loan", "contact"),
                  ("trade", "treasury", "finance", "gl", "regulatory", "cash", "market", "mrm"))
MONEY_TOOLS = ("transfer", "wire", "disburse", "payout", "settle")
# consequence -> floors for (decision_impact, financial_exposure, customer_impact, reversibility, regulatory_exposure)
CONSEQUENCE = {
    "moves_money": (5, 5, 3, 5, 4),
    "blocks_customer_payments": (4, 4, 5, 3, 4),
    "regulatory_record": (4, 2, 2, 3, 5),
    "customer_communication": (3, 2, 4, 4, 3),
    "operational": (2, 2, 1, 2, 2),
    "internal_report": (2, 1, 1, 1, 2),
    "internal_draft": (1, 1, 1, 1, 1),
}
AUTONOMY = {"autonomous": 5, "supervised": 3, "assistive": 2}
LEVEL = {"high": 5, "medium": 3, "low": 1}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def act_id(platform: str, native_id: str) -> str:
    return f"act:{platform}:{hashlib.sha256(native_id.encode()).hexdigest()[:16]}"


def infer_criteria(sa) -> dict:
    """Infer the seven 2026-54 criteria (1-5) from manifest, permissions and source-side criteria."""
    c = sa.criteria or {}
    known = c.get("consequence") in CONSEQUENCE
    dec, fin, cust, rev, reg = CONSEQUENCE.get(c.get("consequence"), (3, 3, 3, 3, 3))
    aut = AUTONOMY.get(c.get("autonomy"), 3)
    if c.get("human_in_loop") is False:
        aut = max(aut, 4)
    cust = max(cust, LEVEL.get(c.get("customer_impact"), 1))
    scopes = " ".join(sa.data_scopes).lower()
    data = 2
    for score, words in zip((5, 4, 3), SENSITIVE_DATA):
        if any(w in scopes for w in words):
            data = score
            break
    if not sa.data_scopes and not known:
        data = 3
    if any(m in t.lower() for t in sa.tools for m in MONEY_TOOLS):
        fin = max(fin, 4)
    if sa.value_limits:
        fin = max(fin, 4)
    return dict(decision_impact=dec, financial_exposure=fin, customer_impact=cust, autonomy=aut,
                data_sensitivity=data, reversibility=rev, regulatory_exposure=reg)


def _limits(vl):
    out = {}
    for tool, v in (vl or {}).items():
        amt = v.get("amount") if isinstance(v, dict) else v
        if isinstance(amt, (int, float)):
            out[tool] = amt
    return out


def _identity(i):
    if not i or i.get("type") in (None, "none"):
        return {"type": "none", "distinct": False}
    subject = i.get("subject") or i.get("principal") or i.get("principalId") or i.get("clientId") or "unknown"
    out = dict(i)
    out.update(subject=str(subject), distinct=bool(i.get("distinct", True)))
    return out


def manifest_of(sa) -> dict:
    return {"tools": list(sa.tools), "data_scopes": list(sa.data_scopes), "systems": list(sa.systems),
            "value_limits": _limits(sa.value_limits), "value_limits_detail": sa.value_limits or {},
            "permissions": {k: v for k, v in (sa.criteria or {}).items()}}


def normalize(sa, tier_override=None) -> dict:
    """Map a SourceAgent into an Agent Record. Tier fields are filled by inference."""
    criteria = infer_criteria(sa)
    res = score_tier(criteria)
    aid = act_id(sa.platform, sa.native_id)
    ts = now_iso()
    versions = [{"version": str(v.get("version")), **{k: x for k, x in v.items() if k != "version" and x is not None}}
                for v in sa.versions if v.get("version") is not None] or [{"version": sa.version}]
    domains = sorted({s.split(".")[0] for s in sa.data_scopes})
    rec = {
        "id": aid, "name": sa.name, "description": sa.description or sa.name, "version": str(sa.version),
        "provider": {"organization": sa.provider or sa.platform},
        "capabilities": {"streaming": False, "pushNotifications": False},
        "skills": [{"id": t, "name": t, "tags": [sa.platform]} for t in sa.tools],
        "oasf": {"skills": list(sa.tools), "domains": domains},
        "source_platform": sa.platform, "source_native_id": sa.native_id, "last_seen": ts,
        "owner": sa.owner or "unassigned", "tier": res.tier, "tier_reasoning": res.reasoning, "criteria": criteria,
        "manifest": manifest_of(sa), "identity": _identity(sa.identity),
        "dependencies": {"models": list(sa.dependencies.get("models", [])),
                         "mcp_servers": list(sa.dependencies.get("mcp_servers", [])),
                         "tools": [{"name": t} for t in sa.tools]},
        "status": "discovered", "versions": versions, "origin": "internal",
        "source_status": sa.status, "source_updated": sa.last_updated, "source_extra": sa.extra,
    }
    if sa.url:
        rec["url"] = sa.url
    return rec


def default_actor():
    """In-process caller: the starting Domino user; system:intake only when unknown."""
    u = os.environ.get("DOMINO_STARTING_USERNAME")
    return (u, u) if u else ("system:intake", "intake")


def from_source(d) -> "SourceAgent":
    """Build a SourceAgent from a raw dict. Server-owned fields are ignored. Raises ValueError if invalid."""
    import dataclasses
    from adapters.base import SourceAgent
    if not isinstance(d, dict):
        raise ValueError("agent must be an object")
    for k in ("native_id", "platform", "name"):
        if not isinstance(d.get(k), str) or not d[k].strip():
            raise ValueError(f"missing or invalid source field: {k}")
    names = {f.name for f in dataclasses.fields(SourceAgent)} - {"id", "tier", "registered_by"}
    kw = {k: v for k, v in d.items() if k in names and k != "status"}
    for k, t in (("tools", list), ("data_scopes", list), ("systems", list), ("versions", list),
                 ("value_limits", dict), ("criteria", dict), ("identity", dict), ("dependencies", dict),
                 ("extra", dict)):
        if k in kw and not isinstance(kw[k], t):
            raise ValueError(f"invalid source field: {k}")
    if any(not isinstance(v, dict) for v in kw.get("versions", [])):
        raise ValueError("invalid source field: versions")
    if any(not isinstance(v, str) for k in ("tools", "data_scopes", "systems") for v in kw.get(k, [])):
        raise ValueError("invalid source field: tools/data_scopes/systems")
    deps = kw.get("dependencies") or {}
    for k in ("models", "mcp_servers", "tools"):
        v = deps.get(k, [])
        if not isinstance(v, list) or any(not isinstance(x, dict) for x in v):
            raise ValueError(f"invalid source field: dependencies.{k} must be a list of objects")
    if d.get("status") is not None:
        kw["status"] = str(d["status"])
    return SourceAgent(**kw)


def _audit(conn, action, target, detail, actor=None):
    actor = actor or default_actor()
    conn.execute(insert(db.audit_events).values(
        actor_user_id=actor[0], actor_username=actor[1], action=action, target=target, ts=now_iso(), detail=detail))


def apply_record(conn, rec: dict, actor=None) -> str:
    """Upsert a normalized record. Returns 'created' | 'updated' | 'retiered'. Re-tiers on manifest change."""
    aid, ts = rec["id"], rec["last_seen"]
    row = conn.execute(select(db.agents).where(db.agents.c.id == aid)).first()
    if row is None:
        conn.execute(insert(db.agents).values(
            id=aid, source_platform=rec["source_platform"], native_id=rec["source_native_id"], name=rec["name"],
            owner=rec["owner"], tier=str(rec["tier"]), tier_reasoning=rec["tier_reasoning"], criteria=rec["criteria"],
            status=rec["status"], last_seen=ts, record=rec))
        conn.execute(insert(db.manifests).values(agent_id=aid, version=rec["version"], content=rec["manifest"], created_at=ts))
        result = "created"
    else:
        old = dict(row.record or {})
        keep = {k: old[k] for k in ("tier", "tier_reasoning", "criteria", "status") if k in old}
        changed = (old.get("manifest") or {}) != rec["manifest"]
        new = {**rec, **keep}
        tier, reasoning, criteria = row.tier, row.tier_reasoning, row.criteria
        result = "updated"
        if changed or tier is None:
            _audit(conn, "manifest-change", aid, {"before": old.get("manifest"), "after": rec["manifest"]}, actor)
            conn.execute(insert(db.manifests).values(agent_id=aid, version=rec["version"], content=rec["manifest"], created_at=ts))
            tier, reasoning, criteria = str(rec["tier"]), rec["tier_reasoning"], rec["criteria"]
            _audit(conn, "retier", aid, {"from": row.tier, "to": tier, "reason": "manifest or permissions changed",
                                         "reasoning": reasoning}, actor)
            if str(row.tier) != tier:
                result = "retiered"
            new.update(tier=int(tier), tier_reasoning=reasoning, criteria=criteria)
        status = row.status if row.status not in (None, "shadow") else rec["status"]
        new["status"] = status
        conn.execute(update(db.agents).where(db.agents.c.id == aid).values(
            name=rec["name"], owner=rec["owner"], tier=tier, tier_reasoning=reasoning, criteria=criteria,
            status=status, last_seen=ts, record=new))
    # identity and dependencies: replace (idempotent, no duplicates)
    ident = rec["identity"]
    conn.execute(delete(db.identities).where(db.identities.c.agent_id == aid))
    conn.execute(insert(db.identities).values(agent_id=aid, kind=ident["type"], principal=ident.get("subject"),
                                              scopes=rec["manifest"].get("data_scopes"), record=ident))
    conn.execute(delete(db.dependencies).where(db.dependencies.c.agent_id == aid))
    for kind, key in (("model", "models"), ("mcp_server", "mcp_servers")):
        for d in rec["dependencies"].get(key, []):
            conn.execute(insert(db.dependencies).values(agent_id=aid, kind=kind, name=d.get("name"),
                                                        target=d.get("registry_version"), record=d))
    have = {r.version for r in conn.execute(select(db.agent_versions.c.version).where(db.agent_versions.c.agent_id == aid))}
    for v in rec["versions"]:
        if v["version"] not in have:
            conn.execute(insert(db.agent_versions).values(agent_id=aid, version=v["version"],
                                                          created_at=v.get("created"), record=v))
    return result
