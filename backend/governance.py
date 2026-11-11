"""Domino Governance sync: one bundle per agent, agentic policy plus model policy, evidence and approval mirroring.

ACT never pushes to git; generated content is recorded as policy evidence answers.
"""
import logging
import os
import threading

import requests
from sqlalchemy import select

from . import db, demo, demo_governance
from .settings import get_setting

log = logging.getLogger(__name__)
TIMEOUT = 15
AGENTIC_NAME = "ACT 2026-54 Agentic Governance"
MODEL_NAME = "SR 26-2 Model Risk (slim)"
KIND_TO_EXTERNAL = {"identity": "act-identity", "tier": "act-tier", "manifest": "act-manifest",
                    "actions": "act-actions", "validation": "act-validation", "drift": "act-monitoring",
                    "findings": "act-monitoring", "override": "act-overrides", "approval": "act-overrides",
                    "evidence_pack": "act-records", "records": "act-records"}
_lock = threading.Lock()
_policy_cache: dict = {}


class GovernanceError(RuntimeError):
    pass


def _base_and_headers():
    proxy = os.environ.get("DOMINO_API_PROXY")
    if proxy:
        return proxy.rstrip("/") + "/api/governance/v1", {}
    host = (os.environ.get("DOMINO_API_HOST") or os.environ.get("DOMINO_USER_HOST") or "").rstrip("/")
    if not host:
        raise GovernanceError("no Domino host configured (DOMINO_API_PROXY or DOMINO_API_HOST)")
    tok = requests.get("http://localhost:8899/access-token", timeout=5).text.strip().strip('"')
    return host + "/api/governance/v1", {"Authorization": f"Bearer {tok}"}


def _call(method, path, body=None, params=None):
    base, headers = _base_and_headers()
    try:
        r = requests.request(method, base + path, json=body, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise GovernanceError(f"{method} {path}: {e}") from e
    code = getattr(r, "status_code", 200)
    if code >= 400:
        raise GovernanceError(f"{method} {path} -> HTTP {code}: {str(getattr(r, 'text', ''))[:200]}")
    try:
        return r.json()
    except ValueError:
        return {}


def _items(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("data", "items", "bundles", "approvals", "results"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def _pname(p):
    if isinstance(p, str):
        return p
    return p.get("policyName") or p.get("name") or ""


def policy_ids() -> dict:
    """{'agentic': id|None, 'model': id|None}: env first, discovery by policy name as fallback."""
    out = {"agentic": get_setting("policy_agentic_id"), "model": get_setting("policy_model_id")}
    if all(out.values()):
        return out
    try:
        overviews = _items(_call("GET", "/policy-overviews"))
    except GovernanceError:
        log.exception("policy discovery failed")
        return out
    for key, name in (("agentic", AGENTIC_NAME), ("model", MODEL_NAME)):
        if not out[key]:
            hit = [p for p in overviews if isinstance(p, dict) and p.get("name") == name
                   and p.get("status", "Published") == "Published"]
            if hit:
                out[key] = hit[-1].get("id")
            else:
                log.warning("policy %r not found by discovery", name)
    return out


def _agent(agent_id):
    with db.engine.connect() as cx:
        a = cx.execute(select(db.agents).where(db.agents.c.id == agent_id)).first()
        deps = cx.execute(select(db.dependencies).where(db.dependencies.c.agent_id == agent_id)).all()
    if a is None:
        raise GovernanceError(f"unknown agent {agent_id}")
    return a, deps


def ensure_bundle(agent_id: str) -> str:
    """Idempotent: one bundle per agent. Stored on agents.bundle_id."""
    if demo.enabled():
        return demo_governance.ensure_bundle(agent_id)
    with _lock:
        try:
            a, deps = _agent(agent_id)
        except GovernanceError:
            a, deps = None, []
        if a is not None and a.bundle_id:
            return a.bundle_id
        name = f"ACT {agent_id}"
        project = os.environ.get("ACT_DOMINO_PROJECT_ID") or os.environ.get("DOMINO_PROJECT_ID")
        bundle = None
        try:
            for b in _items(_call("GET", "/bundles", params={"projectId": project, "name": name})):
                if isinstance(b, dict) and b.get("name") == name and b.get("id"):
                    bundle = b
                    break
        except GovernanceError:
            log.exception("bundle lookup failed")
            raise  # do not create on a failed lookup: avoids duplicate bundles
        if bundle is None:
            ids = policy_ids()
            want_model = bool(a is not None and (a.source_platform == "domino"
                                                 or any(d.kind == "model" for d in deps)))
            body = {"name": name, "projectId": project}
            if ids["agentic"]:
                body["policyId"] = ids["agentic"]
            if want_model and ids["model"]:
                body["additionalPolicyIds"] = [ids["model"]]
            bundle = _call("POST", "/bundles", body)
        bid = bundle.get("id") if isinstance(bundle, dict) else None
        if not bid:
            raise GovernanceError("bundle create returned no id")
        names = [_pname(p) for p in bundle.get("policies") or []]
        if a is not None:
            with db.engine.begin() as cx:
                cx.execute(db.agents.update().where(db.agents.c.id == agent_id).values(
                    bundle_id=bid, governance={"policies": names, "stage": bundle.get("stage")}))
        return bid


def get_bundle(bundle_id):
    if demo.enabled():
        return demo_governance.get_bundle(bundle_id)
    d = _call("GET", f"/bundles/{bundle_id}")
    if isinstance(d, dict) and d.get("id") == bundle_id:
        return d
    for b in _items(d):
        if isinstance(b, dict) and b.get("id") == bundle_id:
            return b
    return d if isinstance(d, dict) and "policies" in d else {}


def _policy_struct(policy_id):
    if policy_id not in _policy_cache:
        _policy_cache[policy_id] = _call("GET", f"/policies/{policy_id}")
    return _policy_cache[policy_id]


def attach_evidence(bundle_id: str, kind: str, payload: dict):
    """kind 'model_version' attaches a ModelVersion; any other kind is answered as policy evidence."""
    if demo.enabled():
        return demo_governance.attach_evidence(bundle_id, kind, payload)
    payload = payload or {}
    if kind == "model_version":
        return _call("POST", f"/bundles/{bundle_id}/attachments", {
            "type": "ModelVersion",
            "identifier": {"name": payload["name"], "version": int(payload["version"])},
            "name": payload.get("name"), "description": payload.get("summary", "")})
    if kind == "report":  # only for files already committed
        return _call("POST", f"/bundles/{bundle_id}/attachments", {
            "type": "Report", "identifier": payload["identifier"],
            "name": payload.get("name"), "description": payload.get("summary", "")})
    ext = KIND_TO_EXTERNAL.get(kind)
    if not ext:
        raise GovernanceError(f"unknown evidence kind {kind}")
    pid = payload.get("policy_id") or policy_ids()["agentic"]
    if not pid:
        raise GovernanceError("agentic policy id unknown")
    ev = None
    for st in _policy_struct(pid).get("stages") or []:
        for e in st.get("evidenceSet") or []:
            if e.get("externalId") == ext:
                ev = e
    if ev is None:
        raise GovernanceError(f"evidence {ext} not in policy")
    arts = ev.get("artifacts") or []
    target = next((x for x in arts if (x.get("details") or {}).get("type") == "textarea"),
                  next((x for x in arts if x.get("artifactType") == "input"), None))
    if target is None:
        raise GovernanceError(f"no input artifact in {ext}")
    text = str(payload.get("summary", ""))
    cite = payload.get("citation") or payload.get("path")
    if cite:
        text += f"\nSource record: {cite}"
    return _call("POST", "/rpc/submit-result-to-policy", {
        "bundleId": bundle_id, "policyId": pid, "evidenceId": ev["id"], "content": {target["id"]: text}})


def expected_policy_names() -> list:
    if demo.enabled():
        return [demo_governance.AGENTIC_NAME, demo_governance.MODEL_NAME]
    return [AGENTIC_NAME, MODEL_NAME]


def mirror_approval(agent_id: str, approver: str) -> str:
    """Best-effort RequestSubmitted then RequestApproved. Returns 'synced' | 'skipped'. Raises GovernanceError."""
    if demo.enabled():
        return demo_governance.mirror_approval(agent_id, approver)
    a, _ = _agent(agent_id)
    bid = a.bundle_id
    if not bid:  # mirror never creates bundles (POST /api/agents/{id}/governance does)
        raise GovernanceError("no governance bundle for this agent yet; approval not mirrored")
    project = os.environ.get("ACT_DOMINO_PROJECT_ID") or os.environ.get("DOMINO_PROJECT_ID")
    aps = [x for x in _items(_call("GET", f"/bundles/{bid}/approvals")) if isinstance(x, dict) and x.get("id")]
    ap = next((x for x in aps if x.get("name") == "Approval to operate"), None)
    if ap is None:
        return "skipped"
    if not ap.get("policyId"):
        raise GovernanceError("approval has no policy id; mirror event skipped")
    for ev in ("RequestSubmitted", "RequestApproved"):
        _call("POST", "/rpc/publish-approval-event", {
            "bundleId": bid, "policyId": ap.get("policyId"), "projectId": project, "eventType": ev,
            "approvalId": ap["id"], "stageApprovalId": ap.get("stageApprovalId"),
            "meta": {"approver": approver, "source": "ACT"}})
    return "synced"
