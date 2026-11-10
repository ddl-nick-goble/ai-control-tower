"""Simulated Governance store for demo mode: local, no HTTP to Domino. Selected in backend/governance.py."""
import hashlib
import threading

from sqlalchemy import select

from . import db
from .demo import GOVERNANCE_LABEL

AGENTIC_NAME = "ACT 2026-54 Agentic Governance"
MODEL_NAME = "SR 26-2 Model Risk"
lock = threading.RLock()
_bundles: dict = {}


def clear():
    with lock:
        _bundles.clear()


def _agent(agent_id):
    with db.engine.connect() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        deps = cx.execute(select(db.dependencies).where(db.dependencies.c.agent_id == agent_id)).all()
    return a, deps


def _entry(bid, name, policies, stage="Evidence collection"):
    return _bundles.setdefault(bid, {
        "id": bid, "name": name, "stage": stage, "simulated": True, "label": GOVERNANCE_LABEL,
        "policies": [{"name": p} for p in policies], "evidence": [], "approvals": []})


def ensure_bundle(agent_id: str) -> str:
    from .governance import GovernanceError
    with lock:
        a, deps = _agent(agent_id)
        if a is None:
            raise GovernanceError(f"unknown agent {agent_id}")
        demo_bid = "demo-" + hashlib.sha256(agent_id.encode()).hexdigest()[:12]
        replace = bool(a.bundle_id) and not str(a.bundle_id).startswith("demo-")  # never relabel a real bundle
        bid = a.bundle_id if a.bundle_id and not replace else demo_bid
        want_model = a.source_platform == "domino" or any(d.kind == "model" for d in deps)
        names = [AGENTIC_NAME] + ([MODEL_NAME] if want_model else [])
        b = _entry(bid, f"ACT {agent_id}", names)
        if not a.bundle_id or replace:
            with db.engine.begin() as cx:
                cx.execute(db.agents.update().where(db.agents.c.id == agent_id).values(
                    bundle_id=bid, governance={"policies": names, "stage": b["stage"]}))
        return bid


def get_bundle(bundle_id):
    with lock:
        b = _bundles.get(bundle_id)
        if b is None and str(bundle_id).startswith("demo-"):
            with db.engine.connect() as cx:  # after a restart: rebuild from the agent row
                a = cx.execute(select(db.agents).where(db.agents.c.bundle_id == bundle_id)).first()
            if a is not None:
                b = _entry(bundle_id, f"ACT {a.id}", (a.governance or {}).get("policies") or [AGENTIC_NAME, MODEL_NAME])
        return dict(b) if b else {}


def attach_evidence(bundle_id: str, kind: str, payload: dict):
    from .governance import GovernanceError
    with lock:
        b = _bundles.get(bundle_id) or (get_bundle(bundle_id) and _bundles.get(bundle_id))
        if not b:
            raise GovernanceError(f"unknown simulated bundle {bundle_id}")
        b["evidence"].append({"kind": kind, "payload": dict(payload or {}), "simulated": True})
        return {"bundleId": bundle_id, "kind": kind, "simulated": True}


def mirror_approval(agent_id: str, approver: str) -> str:
    from .governance import GovernanceError
    with lock:
        a, _ = _agent(agent_id)
        if a is None or not a.bundle_id:
            raise GovernanceError("no governance bundle for this agent yet; approval not mirrored")
        b = _bundles.get(a.bundle_id) or (get_bundle(a.bundle_id) and _bundles.get(a.bundle_id))
        if not b:
            raise GovernanceError(f"bundle {a.bundle_id} is not a simulated demo bundle; approval not mirrored")
        b["approvals"].append({"approver": approver, "simulated": True})
        return "synced"
