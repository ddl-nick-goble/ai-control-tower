"""T-15 tests: demo mode (D-1..D-8, D-10). Written from the spec, API side only.

Contracts assumed: ACT_DEMO_MODE=1 read from env when the app is built/started; GET /api/health gives
demo_mode and stand_ins [{name,label}]; POST /api/demo/approve/{agent_id}, /api/demo/run/{agent_id},
/api/demo/reset (authenticated writes); run response carries the run id as "run_id" (or "id").
"""
import importlib
import json
import socket
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
HDR = {"X-Domino-User-Id": "u1", "X-Domino-User": "tester"}
TABLES = ["agents", "agent_versions", "manifests", "identities", "dependencies", "runs", "events",
          "findings", "approvals", "validation_records", "audit_events"]
STAND_INS = ["llm", "recon_agent_hosting", "governance", "classifier", "service_account", "second_approver"]
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _purge():
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters", "agents")]:
        del sys.modules[m]


def _block_network(monkeypatch):
    """Any non-loopback connection fails the test."""
    import requests
    import httpx

    def host_of(addr):
        return addr[0] if isinstance(addr, tuple) else str(addr)

    real_connect = socket.socket.connect

    def guard(self, addr, *a, **k):
        if self.family != socket.AF_UNIX and host_of(addr) not in LOOPBACK:
            raise AssertionError(f"outbound network connection to {addr!r}")
        return real_connect(self, addr, *a, **k)
    monkeypatch.setattr(socket.socket, "connect", guard)

    def url_guard(url):
        from urllib.parse import urlparse
        h = urlparse(str(url)).hostname
        if h not in LOOPBACK:
            raise AssertionError(f"outbound HTTP call to {url}")

    real_req = requests.Session.request
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, m, u, *a, **k: (url_guard(u), real_req(self, m, u, *a, **k))[1])
    real_send = httpx.Client.send
    monkeypatch.setattr(httpx.Client, "send",
                        lambda self, req, *a, **k: (url_guard(req.url), real_send(self, req, *a, **k))[1])


def _setup(monkeypatch, tmp_path, demo):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("ACT_DB", str(tmp_path / "demo-test.db"))
    monkeypatch.setenv("ACT_DB_PREWARM_COUNT", "0")
    monkeypatch.setenv("ACT_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")
    for k in ("DOMINO_IS_GIT_BASED", "DOMINO_PROJECT_NAME", "DOMINO_API_PROXY", "DOMINO_API_HOST",
              "DOMINO_PROJECT_ID", "ACT_DOMINO_PROJECT_ID", "ACT_DOMINO_SCOPE", "ACT_SERVICE_ACCOUNTS",
              "ACT_LLM_PROVIDER", "ACT_DOMINO_LOCAL_AGENTS", "RECON_AGENT_ID", "ACT_EXTERNAL_ALLOW_HOSTS",
              "ACT_DEMO_MODE"):
        monkeypatch.delenv(k, raising=False)
    for p in ("AWS", "GOOGLE", "MICROSOFT", "DATABRICKS"):
        monkeypatch.delenv(f"ACT_LIVE_{p}", raising=False)
    if demo:
        monkeypatch.setenv("ACT_DEMO_MODE", "1")
    _block_network(monkeypatch)
    _purge()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, True)
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c
    _purge()


@pytest.fixture
def off(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, False)
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c
    _purge()


def _agent(c, name="recon-agent"):
    return next(a for a in c.get("/api/agents").json() if a["name"] == name)


def _counts():
    from backend import db
    with db.engine.connect() as cx:
        return {t: len(cx.execute(getattr(db, t).select()).all()) for t in TABLES}


def _blob(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.text)
    return json.dumps(r.json())


# ---- D-1 ----
def test_d1_health_reports_demo_mode_and_stand_ins(demo):
    h = demo.get("/api/health").json()
    assert h["demo_mode"] is True
    si = h["stand_ins"]
    assert [s["name"] for s in si] == STAND_INS or sorted(s["name"] for s in si) == sorted(STAND_INS)
    for s in si:
        assert set(s) >= {"name", "label"} and s["label"].strip()


def test_d1_defaults_filled_when_unset(demo):
    import os
    assert os.environ.get("ACT_LLM_PROVIDER") == "mock"
    assert os.environ.get("ACT_DOMINO_LOCAL_AGENTS") == "config/local_agents.json"
    assert os.environ.get("RECON_AGENT_ID") == "local-recon-agent"
    assert os.environ.get("ACT_EXTERNAL_ALLOW_HOSTS") == "127.0.0.1,localhost"


def test_d1_defaults_do_not_override_set_variables(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, True)
    monkeypatch.setenv("ACT_EXTERNAL_ALLOW_HOSTS", "example.test")
    monkeypatch.setenv("RECON_AGENT_ID", "custom-id")
    main = importlib.import_module("backend.main")
    import os
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["demo_mode"] is True
        assert os.environ["ACT_EXTERNAL_ALLOW_HOSTS"] == "example.test"
        assert os.environ["RECON_AGENT_ID"] == "custom-id"
    _purge()


def test_d1_app_sh_accepts_demo_flag():
    assert "--demo" in (ROOT / "app.sh").read_text()


# ---- D-2 ----
def test_d2_autoseed_demo_state(demo):
    names = {a["name"] for a in demo.get("/api/agents").json()}
    assert "recon-agent" in names and "treasury-sweep-agent" in names
    assert len(names) == 12
    ts = _agent(demo, "treasury-sweep-agent")
    open_sd = [f for f in demo.get(f"/api/agents/{ts['id']}/findings").json()
               if f["status"] == "open" and f["type"] == "scope_drift"]
    assert len(open_sd) == 1
    rid = _agent(demo)["id"]
    assert "mock-llm" in _blob(demo, f"/api/agents/{rid}/validation")
    assert demo.get(f"/api/agents/{rid}/runs").json()


def test_d2_restart_does_not_duplicate(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, True)
    main = importlib.import_module("backend.main")
    with TestClient(main.app):
        first = _counts()
    _purge()
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        assert _counts() == first
        assert len(c.get("/api/agents").json()) == 12
    _purge()


# ---- D-3 ----
def test_d3_governance_stand_in_local_store(demo):
    from backend import governance
    rid = _agent(demo)["id"]
    bid = governance.ensure_bundle(rid)
    assert bid.startswith("demo-")
    assert governance.ensure_bundle(rid) == bid
    b = governance.get_bundle(bid)
    blob = json.dumps(b, default=str)
    assert "ACT 2026-54 Agentic Governance" in blob and "SR 26-2 Model Risk" in blob
    governance.attach_evidence(bid, "evidence_pack", {"note": "x"})
    governance.mirror_approval(rid, "demo-approver-simulated")
    assert governance.get_bundle(bid)


def test_d3_governance_endpoint_and_evidence_label(demo):
    rid = _agent(demo)["id"]
    r = demo.post(f"/api/agents/{rid}/governance", headers=HDR)
    assert r.status_code == 200
    assert r.json()["bundle_id"].startswith("demo-")
    names = [p["name"] for p in r.json()["policies"]]
    assert "ACT 2026-54 Agentic Governance" in names and "SR 26-2 Model Risk" in names
    md = _pack_md(demo, rid)
    assert "simulated Governance (demo)" in md


def _pack_md(c, aid):
    r = c.post(f"/api/agents/{aid}/evidence-pack", headers=HDR)
    assert r.status_code == 200, r.text
    pid = r.json().get("pack_id") or r.json().get("id")
    m = c.get(f"/api/evidence-packs/{pid}/markdown")
    assert m.status_code == 200
    return m.text


# ---- D-4 ----
def test_d4_demo_classifier_source_on_spans_and_dependency(demo):
    rid = _agent(demo)["id"]
    run = demo.post(f"/api/demo/run/{rid}", headers=HDR).json()
    run_id = run.get("run_id") or run.get("id")
    tl = demo.get(f"/api/runs/{run_id}/timeline").json()
    srcs = [(e.get("attrs") or {}).get("act.classifier.source") for e in tl["events"]]
    srcs = [s for s in srcs if s]
    assert srcs and set(srcs) == {"demo"}
    deps = demo.get(f"/api/agents/{rid}/dependencies").json()
    bc = [m for m in deps["models"] if m["name"] == "break_classifier"]
    assert bc and "champion" in json.dumps(bc[0])


# ---- D-5 ----
def test_d5_service_account_stand_in(demo):
    rec = demo.get(f"/api/agents/{_agent(demo)['id']}").json()
    ident = rec["identity"]
    assert ident["subject"] == "svc-act-recon-demo"
    assert ident["distinct"] is True


def test_d5_evidence_pack_has_no_missing_identity_warning(demo):
    md = _pack_md(demo, _agent(demo)["id"])
    assert "svc-act-recon-demo" in md
    assert "No distinct identity recorded" not in md
    assert "simulated service account (demo)" in md


# ---- D-6 ----
def test_d6_demo_approve_as_simulated_approver(demo):
    rid = _agent(demo)["id"]
    from backend import db
    with db.engine.connect() as cx:
        reg = cx.execute(select(db.agents.c.registered_by).where(db.agents.c.id == rid)).scalar()
    assert demo.post(f"/api/agents/{rid}/approve",
                     headers={"X-Domino-User-Id": reg, "X-Domino-User": reg}).status_code == 403
    r = demo.post(f"/api/demo/approve/{rid}", headers=HDR)
    assert r.status_code == 200, r.text
    assert _agent(demo)["status"] == "approved"
    with db.engine.connect() as cx:
        aps = cx.execute(db.approvals.select().where(db.approvals.c.agent_id == rid)).all()
    assert len(aps) == 1 and aps[0].approver == "demo-approver-simulated"
    assert "simulated" in json.dumps(r.json()).lower()
    md = _pack_md(demo, rid).lower()
    assert "demo-approver-simulated" in md and "simulated" in md.split("approvals and overrides", 1)[-1]


def test_d6_demo_approve_requires_auth(demo):
    assert demo.post(f"/api/demo/approve/{_agent(demo)['id']}").status_code == 401


# ---- D-7 ----
def test_d7_run_recon_agent_adds_run_with_timeline(demo):
    rid = _agent(demo)["id"]
    before = {r.get("run_id") or r.get("id") for r in demo.get(f"/api/agents/{rid}/runs").json()}
    r = demo.post(f"/api/demo/run/{rid}", headers=HDR)
    assert r.status_code == 200, r.text
    run_id = r.json().get("run_id") or r.json().get("id")
    assert run_id and run_id not in before
    after = {x.get("run_id") or x.get("id") for x in demo.get(f"/api/agents/{rid}/runs").json()}
    assert run_id in after
    tl = demo.get(f"/api/runs/{run_id}/timeline")
    assert tl.status_code == 200 and tl.json()["events"]


# ---- D-8 ----
def test_d8_reset_restores_seed_state(demo):
    rid = _agent(demo)["id"]
    base = _counts()
    demo.post(f"/api/demo/run/{rid}", headers=HDR)
    demo.post(f"/api/demo/approve/{rid}", headers=HDR)
    assert _counts() != base
    r = demo.post("/api/demo/reset", headers=HDR)
    assert r.status_code == 200, r.text
    assert _agent(demo)["status"] in ("discovered", "pending_review")
    assert _counts() == base
    assert len(demo.get("/api/agents").json()) == 12
    ts = _agent(demo, "treasury-sweep-agent")["id"]
    assert len([f for f in demo.get(f"/api/agents/{ts}/findings").json()
                if f["status"] == "open" and f["type"] == "scope_drift"]) == 1


# ---- D-10 ----
def test_d10_off_by_default_no_demo_surface(off):
    h = off.get("/api/health").json()
    assert not h.get("demo_mode")
    assert not h.get("stand_ins")
    assert off.get("/api/demo/state").status_code == 404
    for p in ("/api/demo/reset", "/api/demo/approve/x", "/api/demo/run/x"):
        assert off.post(p, headers=HDR).status_code == 404
    assert off.get("/api/agents").json() == []  # no auto-seed


def test_d10_off_governance_is_not_the_simulated_store(off, monkeypatch):
    from backend import governance
    monkeypatch.delenv("DOMINO_API_HOST", raising=False)
    try:
        bid = governance.ensure_bundle("act:domino:nonexistent")
    except Exception:
        return  # real client, no Domino configured
    assert not str(bid).startswith("demo-")


# ---- spec points added: shared-DB guard, env fill, audit identity ----
def _audit_rows(action):
    from backend import db
    with db.engine.connect() as cx:
        return cx.execute(select(db.audit_events).where(db.audit_events.c.action == action)).all()


def _insert_plain_agent(name="plain-agent"):
    from backend import db
    with db.engine.begin() as cx:
        cx.execute(db.agents.insert().values(
            id=f"act:test:{name}", source_platform="test", native_id=name, name=name, owner="o", tier="low",
            status="discovered", registered_by="regu", registered_by_username="regu", last_seen="2026-01-01T00:00:00+00:00",
            record={"name": name}))


def test_demo_reset_never_wipes_non_demo_db(tmp_path, monkeypatch):
    # phase 1: demo off, create a non-demo DB holding real-looking agents
    _setup(monkeypatch, tmp_path, False)
    main = importlib.import_module("backend.main")
    with TestClient(main.app):
        _insert_plain_agent("plain-1")
        _insert_plain_agent("plain-2")
        before = _counts()
    assert before["agents"] == 2
    _purge()
    # phase 2: demo on, same DB file
    monkeypatch.setenv("ACT_DEMO_MODE", "1")
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        names = {a["name"] for a in c.get("/api/agents").json()}
        assert names == {"plain-1", "plain-2"}  # startup did not seed
        assert _counts() == before
        r = c.post("/api/demo/reset", headers=HDR)
        assert r.status_code == 409, r.text
        assert _counts() == before
        assert {a["name"] for a in c.get("/api/agents").json()} == {"plain-1", "plain-2"}
    _purge()


def test_demo_defaults_leave_explicitly_empty_variable(tmp_path, monkeypatch):
    import os
    _setup(monkeypatch, tmp_path, True)
    monkeypatch.setenv("RECON_AGENT_ID", "")
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["demo_mode"] is True
        assert os.environ["RECON_AGENT_ID"] == ""
        # unset ones still filled
        assert os.environ.get("ACT_LLM_PROVIDER") == "mock"
    _purge()


def test_demo_approval_audit_identifies_real_user_and_simulated(demo):
    rid = _agent(demo)["id"]
    r = demo.post(f"/api/demo/approve/{rid}", headers=HDR)
    assert r.status_code == 200, r.text
    rows = _audit_rows("approved")
    assert len(rows) == 1
    row = rows[0]
    blob = json.dumps(row.detail)
    assert row.actor_user_id == "demo-approver-simulated"
    assert row.detail.get("simulated") is True
    assert row.detail.get("requested_by") == "u1"
    assert "tester" in blob


def test_normal_approve_ignores_client_supplied_audit_fields(off):
    from backend import db
    _insert_plain_agent("plain-x")
    aid = "act:test:plain-x"
    hdr = {"X-Domino-User-Id": "approver1", "X-Domino-User": "approver1"}
    r = off.post(f"/api/agents/{aid}/approve", headers=hdr,
                 json={"simulated": True, "requested_by": "evil", "requested_by_username": "evil",
                       "audit_extra": {"simulated": True}, "run_id": None})
    assert r.status_code == 200, r.text
    rows = _audit_rows("approved")
    assert len(rows) == 1
    d = rows[0].detail
    assert "simulated" not in d and "requested_by" not in d and "requested_by_username" not in d
    assert "evil" not in json.dumps(d)
    assert rows[0].actor_user_id == "approver1"
