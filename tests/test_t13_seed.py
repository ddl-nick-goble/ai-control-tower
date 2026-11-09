"""T-13 tests: demo seed, second-approver helper, docs (demo script, HANDOFF Section 6)."""
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_t8_governance import FakeDomino, _Resp  # noqa: E402

TABLES = ["agents", "agent_versions", "manifests", "identities", "dependencies", "runs", "events",
          "findings", "approvals", "validation_records", "evidence_packs", "audit_events"]


class NoAppsDomino(FakeDomino):
    """Governance fake whose Apps API lists nothing, so only the local recon-agent is Domino-sourced."""

    def __call__(self, *a, **k):
        url = next((x for x in a if isinstance(x, str) and x.startswith("http")), "")
        if url.endswith("/api/apps/v1/apps"):
            return _Resp({"items": []})
        return super().__call__(*a, **k)


@pytest.fixture
def fake(monkeypatch):
    import requests
    f = NoAppsDomino()
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
def demo_env(db_env, fake, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("ACT_DOMINO_LOCAL_AGENTS", "config/local_agents.json")
    monkeypatch.setenv("ACT_EXTERNAL_ALLOW_HOSTS", "127.0.0.1,localhost")
    for p in ("AWS", "GOOGLE", "MICROSOFT", "DATABRICKS"):
        monkeypatch.delenv(f"ACT_LIVE_{p}", raising=False)
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters")]:
        del sys.modules[m]
    return db_env


def _seed_mod():
    return importlib.import_module("jobs.seed_demo")


@pytest.fixture
def seeded(demo_env):
    mod = _seed_mod()
    mod.seed(db_path=str(demo_env))
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c, mod


def _by_name(c, name):
    return next(a for a in c.get("/api/agents").json() if a["name"] == name)


def test_ac1_seed_is_runnable_script_and_function():
    src = (ROOT / "jobs" / "seed_demo.py").read_text()
    assert "def seed(" in src and "__main__" in src


def test_ac1_seed_fleet_counts(seeded):
    c, _ = seeded
    fleet = c.get("/api/agents").json()
    assert len(fleet) == 12
    counts = {}
    for a in fleet:
        counts[a["source_platform"]] = counts.get(a["source_platform"], 0) + 1
    assert counts == {"aws": 3, "google": 3, "microsoft": 3, "databricks": 2, "domino": 1}
    assert not any(a.get("origin") == "external" for a in fleet)


def test_ac2_recon_agent_record(seeded):
    c, _ = seeded
    a = _by_name(c, "recon-agent")
    assert a["tier"] == 2
    assert a["status"] in ("discovered", "pending_review")
    rec = c.get(f"/api/agents/{a['id']}").json()
    assert len(rec["manifest"]["tools"]) == 3
    deps = c.get(f"/api/agents/{a['id']}/dependencies").json()
    bc = [m for m in deps["models"] if m["name"] == "break_classifier"]
    assert bc and bc[0].get("registry_version")
    v = c.get(f"/api/agents/{a['id']}/validation")
    assert v.status_code == 200
    blob = json.dumps(v.json())
    for k in ("mean", "p5", "p95", "pass_rate"):
        assert k in blob
    runs = c.get(f"/api/agents/{a['id']}/runs").json()
    assert runs
    from backend import db
    with db.engine.connect() as cx:
        row = cx.execute(db.agents.select().where(db.agents.c.id == a["id"])).first()
    assert row.registered_by


def test_ac3_treasury_sweep_open_scope_drift(seeded):
    c, _ = seeded
    a = _by_name(c, "treasury-sweep-agent")
    assert a["tier"] == 1
    fs = [f for f in c.get(f"/api/agents/{a['id']}/findings").json()
          if f["status"] == "open" and f["type"] == "scope_drift"]
    assert len(fs) == 1
    f = fs[0]
    assert f["ts"].startswith("2026-11-07") and "02:14" in f["ts"]
    reasons = {v["tool"]: v["reason"] for v in f["detail"]["violations"]}
    assert reasons.get("transfer_funds") == "value_limit_exceeded"
    assert reasons.get("list_credentials") == "tool_not_in_manifest"
    assert f["explanation"].strip()


def test_ac4_no_governance_calls_by_default(demo_env, fake):
    _seed_mod().seed(db_path=str(demo_env), governance=False)
    assert [c for c in fake.calls if "/api/governance/v1" in c[1]] == []


def test_ac4_governance_true_creates_bundles_before_approval(demo_env, fake):
    mod = _seed_mod()
    mod.seed(db_path=str(demo_env), governance=True)
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        posts = [x for x in fake.calls if x[0] == "POST"]
        assert len(fake.bundles) >= 2
        assert posts
        for n in ("recon-agent", "treasury-sweep-agent"):
            g = c.get(f"/api/agents/{_by_name(c, n)['id']}/governance").json()
            assert g.get("bundle_id")
        from backend import db
        with db.engine.connect() as cx:
            assert cx.execute(db.approvals.select()).fetchall() == []


def test_ac5_idempotent_and_chain_verifies(seeded):
    c, mod = seeded
    mod.seed(db_path=str(Path(__import__("os").environ["ACT_DB"])))
    assert len(c.get("/api/agents").json()) == 12
    a = _by_name(c, "treasury-sweep-agent")
    open_f = [f for f in c.get(f"/api/agents/{a['id']}/findings").json() if f["status"] == "open"]
    assert len(open_f) == 1
    v = c.get("/api/audit/verify").json()
    assert v.get("ok") is True


def test_ac6_seed_does_not_approve_and_helper_approves_as_second_user(seeded):
    c, mod = seeded
    a = _by_name(c, "recon-agent")
    assert a["status"] in ("discovered", "pending_review")
    from backend import db
    with db.engine.connect() as cx:
        assert cx.execute(db.approvals.select().where(db.approvals.c.agent_id == a["id"])).fetchall() == []
        reg = cx.execute(db.agents.select().where(db.agents.c.id == a["id"])).first().registered_by
    # the seed user may not approve
    r = c.post(f"/api/agents/{a['id']}/approve", headers={"X-Domino-User-Id": reg, "X-Domino-User": reg})
    assert r.status_code == 403
    expiry = mod.approve_as_second_user(a["id"])
    assert expiry
    assert _by_name(c, "recon-agent")["status"] == "approved"
    with db.engine.connect() as cx:
        ap = cx.execute(db.approvals.select().where(db.approvals.c.agent_id == a["id"])).first()
    assert "demo-approver" in ap.approver
    assert ap.expires_at


def test_ac7_architecture_doc_has_three_diagrams():
    p = ROOT / "docs" / "architecture.md"
    assert p.exists()
    blocks = re.findall(r"```mermaid\n(.*?)```", p.read_text(), re.S)
    assert len(blocks) == 3
    assert sum(b.lstrip().startswith(("flowchart", "graph")) for b in blocks) == 1
    assert sum(b.lstrip().startswith("sequenceDiagram") for b in blocks) == 1
    er = [b for b in blocks if b.lstrip().startswith("erDiagram")]
    assert len(er) == 1
    for t in TABLES:
        assert re.search(rf"\b{t}\b", er[0]), t


def test_ac7_demo_doc_covers_steps_and_disclosures():
    p = ROOT / "docs" / "DEMO.md"
    assert p.exists()
    t = p.read_text().lower()
    for s in ("fleet", "recon-agent", "approve", "treasury-sweep-agent", "timeline", "evidence pack",
              "register", "external", "passport", "mock", "local"):
        assert s in t, s


def test_ac7_readme_mentions_entrypoints():
    t = (ROOT / "README.md").read_text()
    for s in ("seed_demo", "app.sh", "docs/INTEGRATION.md"):
        assert s in t, s
