"""T-8 tests: governance sync, approvals, dependencies (AC-8, AC-9)."""
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
DOMINO_ID = "dom-agent-001"
REG = {"X-Domino-User-Id": "registrar", "X-Domino-User": "Registrar"}
OTHER = {"X-Domino-User-Id": "approver", "X-Domino-User": "Approver"}
POLICIES = ["ACT 2026-54 Agentic Policy", "SR 26-2 Model Policy"]


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class FakeDomino:
    """In-memory stand-in for the Domino Governance API plus the Apps API used by intake."""

    def __init__(self):
        self.bundles = {}

    def __call__(self, *a, **k):
        args = [x for x in a if isinstance(x, str)]
        url = next((x for x in args if x.startswith("http")), "")
        method = next((x for x in args if x.upper() in ("GET", "POST", "PUT", "PATCH")), "GET").upper()
        if "8899" in url:
            return _Resp("tok")
        if "/api/governance/v1" in url:
            if method == "GET":
                return _Resp({"items": list(self.bundles.values()), "data": list(self.bundles.values()), "bundles": list(self.bundles.values())})
            bid = f"bundle-{len(self.bundles) + 1}"
            b = {"id": bid, "name": "b", "policies": [{"name": n, "policyName": n} for n in POLICIES], "approvals": []}
            self.bundles[bid] = b
            return _Resp(b)
        if url.endswith("/api/apps/v1/apps"):
            it = {"id": DOMINO_ID, "agentId": DOMINO_ID, "appId": DOMINO_ID, "name": "domino-deployed-agent",
                  "ownerName": "dom-owner", "owner": "dom-owner", "status": "Running", "description": "stub",
                  "versions": [], "lastUpdated": "2026-11-05T00:00:00Z",
                  "currentVersion": {"versionNumber": 1, "currentInstance": {"publisher": {"name": "dom-owner"}}}}
            return _Resp({"items": [it]})
        return _Resp({"items": []})


@pytest.fixture
def fake(monkeypatch):
    import requests
    f = FakeDomino()
    monkeypatch.setenv("DOMINO_API_HOST", "http://domino.invalid")
    monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-123")
    for k in ("ACT_DOMINO_PROJECT_ID", "ACT_DOMINO_SCOPE", "ACT_SERVICE_ACCOUNTS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(requests, "get", lambda u, *a, **k: f(u, **k))
    monkeypatch.setattr(requests, "post", lambda u, *a, **k: f("POST", u, **k))
    monkeypatch.setattr(requests, "request", lambda m, u, *a, **k: f(m, u, **k))
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: f(m, u, **k))
    monkeypatch.setattr(requests.Session, "get", lambda self, u, *a, **k: f(u, **k))
    monkeypatch.setattr(requests.Session, "post", lambda self, u, *a, **k: f("POST", u, **k))
    return f


@pytest.fixture
def client(db_env, fake):
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


def _seed(agent_id, tier="2", registered_by="registrar", status="pending_review"):
    from backend import db
    with db.engine.begin() as cx:
        cx.execute(db.agents.insert().values(
            id=agent_id, source_platform="aws", native_id=agent_id, name=agent_id, owner="o",
            tier=tier, status=status, registered_by=registered_by, record={}))


def _domino_agent():
    importlib.import_module("jobs.intake").run_intake()
    from backend import db
    with db.engine.begin() as cx:
        aid = cx.execute(db.agents.select().where(db.agents.c.native_id == DOMINO_ID)).first().id
        cx.execute(db.dependencies.delete().where(db.dependencies.c.agent_id == aid))
        cx.execute(db.dependencies.insert().values(
            agent_id=aid, kind="model", name="break_classifier", target="2",
            record={"name": "break_classifier", "registry_version": "2"}))
    return aid


# ---------------- policies ----------------

@pytest.mark.parametrize("fn", ["act-2026-54.yaml", "sr-26-2-model.yaml"])
def test_ac8_policy_files_parse_with_name_and_sections(fn):
    p = ROOT / "policies" / fn
    assert p.exists()
    d = yaml.safe_load(p.read_text())
    assert d.get("name")
    assert "stages" in d or "evidence" in d


def test_ac8_policy_names_identify_frameworks():
    a = yaml.safe_load((ROOT / "policies" / "act-2026-54.yaml").read_text())
    m = yaml.safe_load((ROOT / "policies" / "sr-26-2-model.yaml").read_text())
    assert "2026-54" in a["name"]
    assert "SR 26-2" in m["name"]


# ---------------- AC-8 ----------------

def test_ac8_governance_module_interface():
    g = importlib.import_module("backend.governance")
    assert callable(g.ensure_bundle) and callable(g.attach_evidence)


def test_ac8_governance_shows_both_policies_same_bundle(client):
    aid = _domino_agent()
    r = client.get(f"/api/agents/{aid}/governance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bundle_id"]
    names = [p["name"] if isinstance(p, dict) else p for p in body["policies"]]
    assert any("2026-54" in n for n in names)
    assert any("SR 26-2" in n for n in names)
    # both on the same bundle: stable across calls
    assert client.get(f"/api/agents/{aid}/governance").json()["bundle_id"] == body["bundle_id"]


def test_ac8_dependency_graph_lists_model_with_registry_version(client):
    aid = _domino_agent()
    r = client.get(f"/api/agents/{aid}/dependencies")
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("models", "mcp_servers", "tools"):
        assert k in body
    m = next(x for x in body["models"] if x["name"] == "break_classifier")
    assert str(m["registry_version"]) == "2"


# ---------------- AC-9 ----------------

def _audit(action=None):
    from backend import db
    with db.engine.connect() as cx:
        rows = cx.execute(db.audit_events.select()).fetchall()
    return [r for r in rows if action is None or r.action == action]


def test_ac9_registrant_approve_refused_403_and_audited(client):
    _seed("a1")
    r = client.post("/api/agents/a1/approve", headers=REG)
    assert r.status_code == 403
    rows = _audit("approve_refused")
    assert len(rows) == 1 and rows[0].actor_user_id == "registrar" and rows[0].target == "a1"
    from backend import db
    with db.engine.connect() as cx:
        assert cx.execute(db.agents.select()).first().status == "pending_review"
        assert cx.execute(db.approvals.select()).fetchall() == []


def test_ac9_anonymous_approve_401(client):
    _seed("a1")
    assert client.post("/api/agents/a1/approve").status_code == 401


@pytest.mark.parametrize("tier,days", [("1", 90), ("2", 180), ("3", 365), ("4", 365)])
def test_ac9_different_user_approves_with_tier_expiry(client, tier, days):
    _seed("a1", tier=tier)
    r = client.post("/api/agents/a1/approve", headers=OTHER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    exp = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = exp - datetime.now(timezone.utc)
    assert abs(delta - timedelta(days=days)) <= timedelta(days=1, hours=1)
    from backend import db
    with db.engine.connect() as cx:
        assert cx.execute(db.agents.select()).first().status == "approved"
        ap = cx.execute(db.approvals.select()).fetchall()
    assert len(ap) == 1 and ap[0].approver == "approver" and ap[0].expires_at
    assert len(_audit("approved")) == 1
    assert client.get("/api/agents/a1").json()["status"] == "approved"


# ---------------- timeline merge ----------------

def _seed_run_events(run_id, agent_id):
    from backend import db, chain
    items = []
    for i in range(2):
        ts = f"2026-11-0{i + 1}T00:00:00Z"
        items.append({"run_id": run_id, "agent_id": agent_id, "ts": ts, "kind": "tool_call", "span_id": f"s{i}",
                      "raw_span": {}, "event": {"run_id": run_id, "agent_id": agent_id, "ts": ts, "kind": "tool_call",
                                                "name": f"t{i}", "args_redacted": {}}})
    with chain.lock, db.engine.connect() as cx:
        chain.begin_write(cx)
        chain.append_events(cx, items)
        cx.commit()


def test_ac9_timeline_includes_override_and_approval_ordered(client):
    _seed("a1")
    _seed_run_events("run-1", "a1")
    r = client.post("/api/runs/run-1/override", json={"reason": "manual stop"}, headers=OTHER)
    assert r.status_code in (200, 201), r.text
    r = client.post("/api/agents/a1/approve", json={"run_id": "run-1"}, headers=OTHER)
    assert r.status_code == 200, r.text
    tl = client.get("/api/runs/run-1/timeline").json()
    kinds = [e["kind"] for e in tl["events"]]
    assert "approval" in kinds and "override" in kinds and kinds.count("tool_call") == 2
    ts = [e["ts"] for e in tl["events"]]
    assert ts == sorted(ts)


def test_ac9_override_requires_identity(client):
    _seed("a1")
    _seed_run_events("run-1", "a1")
    assert client.post("/api/runs/run-1/override", json={"reason": "x"}).status_code == 401


# ---------------- live ----------------

_LIVE_PROXY = os.environ.get("DOMINO_API_PROXY")


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("ACT_GOVERNANCE_LIVE") != "1", reason="ACT_GOVERNANCE_LIVE not set")
def test_ac8_live_ensure_bundle_attaches_both_policies(db_mod, monkeypatch):
    import requests
    if _LIVE_PROXY:
        monkeypatch.setenv("DOMINO_API_PROXY", _LIVE_PROXY)
    db_mod.init_db()
    aid = "act:test:live-governance"
    with db_mod.engine.begin() as cx:
        cx.execute(db_mod.agents.insert().values(
            id=aid, source_platform="domino", native_id=aid, name=aid, owner="test-user",
            tier="2", status="pending_review", registered_by="test-user", record={}))
        cx.execute(db_mod.dependencies.insert().values(
            agent_id=aid, kind="model", name="break_classifier", target="2",
            record={"name": "break_classifier", "registry_version": "2"}))
    g = importlib.import_module("backend.governance")
    bid = None
    try:
        bid = g.ensure_bundle(aid)
        assert bid
        assert g.ensure_bundle(aid) == bid
        # read the bundle back from the real Governance API
        assert _LIVE_PROXY, "DOMINO_API_PROXY required for live readback"
        r = requests.get(f"{_LIVE_PROXY}/api/governance/v1/bundles/{bid}", timeout=30)
        assert r.status_code == 200, r.text[:300]
        b = r.json()
        assert b["id"] == bid
        pnames = [p.get("policyName") or "" for p in b.get("policies") or []]
        assert any("2026-54" in n for n in pnames), pnames
        assert any("SR 26-2" in n for n in pnames), pnames
        # the app route reports the same bundle and both policies
        main = importlib.import_module("backend.main")
        with TestClient(main.app) as c:
            rr = c.get(f"/api/agents/{aid}/governance")
        assert rr.status_code == 200, rr.text
        body = rr.json()
        assert body["bundle_id"] == bid, body
        rnames = [p["name"] if isinstance(p, dict) else p for p in body["policies"]]
        assert any("2026-54" in n for n in rnames), body
        assert any("SR 26-2" in n for n in rnames), body
    finally:
        if bid and _LIVE_PROXY:
            r = requests.patch(f"{_LIVE_PROXY}/api/governance/v1/bundles/{bid}", json={"state": "Archived"}, timeout=30)
            print("ARCHIVED", bid, r.status_code, r.text[:200])
