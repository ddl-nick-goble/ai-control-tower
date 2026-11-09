"""T-10 tests: evidence pack generator (AC-7, AC-12 pack part)."""
import importlib
import json
import re
import sys
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from test_t8_governance import FakeDomino, DOMINO_ID  # noqa: F401  (fake Governance API)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "evidence-pack.json").read_text())
DRIFT = json.loads((ROOT / "fixtures" / "runs" / "weekend-drift.json").read_text())
ALICE = {"X-Domino-User-Id": "alice-id", "X-Domino-User": "alice"}
BOB = {"X-Domino-User-Id": "bob-id", "X-Domino-User": "bob"}
ORDER = ["identity and ownership", "tier and criteria", "permitted actions", "validation",
         "monitoring and findings", "approvals and overrides", "records and retention"]
TABLES = ["agents", "agent_versions", "approvals", "audit_events", "dependencies", "events", "evidence_packs",
          "findings", "identities", "manifests", "overrides", "runs", "validation_records"]


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
def evdir(tmp_path, monkeypatch):
    d = tmp_path / "evidence"
    monkeypatch.setenv("ACT_EVIDENCE_DIR", str(d))
    return d


@pytest.fixture
def client(db_env, fake, evdir):
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        importlib.import_module("jobs.intake").run_intake()
        yield c


def _agent(client, name="treasury-sweep-agent"):
    return next(a for a in client.get("/api/agents").json() if a["name"] == name)["id"]


def _gen(client, aid, headers=ALICE):
    return client.post(f"/api/agents/{aid}/evidence-pack", headers=headers)


def _pack(client, aid):
    from backend import db
    with db.engine.connect() as cx:
        row = cx.execute(db.evidence_packs.select().where(db.evidence_packs.c.agent_id == aid)
                         .order_by(db.evidence_packs.c.id.desc())).first()
    assert row is not None, "no evidence_packs row"
    c = row.content
    return json.loads(c) if isinstance(c, str) else c


def _section(pack, sid):
    return next(s for s in pack["sections"] if s["id"] == sid)


def _ingest_drift(client):
    assert client.post("/v1/traces", json=DRIFT, headers=ALICE).status_code == 200


def test_ac7_generates_markdown_and_pdf_in_order(client):
    aid = _agent(client)
    r = _gen(client, aid)
    assert r.status_code == 200, r.text
    b = r.json()
    assert {"markdown_path", "pdf_path", "bundle_id"} <= set(b)
    md, pdf = Path(b["markdown_path"]), Path(b["pdf_path"])
    assert md.is_file() and pdf.is_file()
    assert pdf.read_bytes()[:4] == b"%PDF"
    heads = [h.lower() for h in re.findall(r"^##\s+(.+)$", md.read_text(), re.M)]
    assert len(heads) == 7, heads
    for h, want in zip(heads, ORDER):
        assert want in h, (heads, want)


def test_ac7_stored_pack_validates_and_cites_act_records(client):
    aid = _agent(client)
    assert _gen(client, aid).status_code == 200
    pack = _pack(client, aid)
    jsonschema.validate(pack, SCHEMA)
    assert pack["agent_id"] == aid
    for s in pack["sections"]:
        assert s["citations"], s["id"]
        for c in s["citations"]:
            c = c if isinstance(c, str) else json.dumps(c)
            assert any(re.search(rf"\b{t}\b", c) for t in TABLES), c
    assert any(f"agents:{aid}" in json.dumps(_section(pack, "identity_ownership")["citations"]) for _ in [0])


def test_ac7_files_under_evidence_dir_with_agent_and_timestamp(client, evdir):
    aid = _agent(client)
    b = _gen(client, aid).json()
    safe = re.sub(r"[^A-Za-z0-9]", "", aid)
    for p in (b["markdown_path"], b["pdf_path"]):
        p = Path(p)
        assert evdir in p.parents
        assert re.search(r"\d{8}", p.name) or re.search(r"\d{4}-\d{2}-\d{2}", p.name), p.name
        assert re.sub(r"[^A-Za-z0-9]", "", str(p.relative_to(evdir))).find(safe) >= 0 or "treasury" in str(p)
    assert Path(b["markdown_path"]).suffix == ".md" and Path(b["pdf_path"]).suffix == ".pdf"


def test_ac12_pack_monitoring_names_scope_drift_finding(client):
    aid = _agent(client)
    _ingest_drift(client)
    fs = [f for f in client.get(f"/api/agents/{aid}/findings").json() if f["type"] == "scope_drift"]
    assert fs
    assert _gen(client, aid).status_code == 200
    sec = _section(_pack(client, aid), "monitoring_findings")
    text = sec["content"] if isinstance(sec["content"], str) else json.dumps(sec["content"])
    assert "scope_drift" in text and "transfer_funds" in text and "list_credentials" in text
    assert re.search(r"2026-11-07[ T]02:14", text)
    cites = json.dumps(sec["citations"])
    assert any(f"findings:{f['id']}" in cites for f in fs)
    md = Path(_gen(client, aid).json()["markdown_path"]).read_text()
    assert "transfer_funds" in md and "list_credentials" in md


def test_ac7_approvals_section_names_approver_and_expiry(client):
    aid = _agent(client)
    r = client.post(f"/api/agents/{aid}/approve", headers=BOB)
    assert r.status_code == 200, r.text
    exp = r.json()["expires_at"][:10]
    assert _gen(client, aid).status_code == 200
    sec = _section(_pack(client, aid), "approvals_overrides")
    text = json.dumps(sec["content"])
    assert "bob" in text and exp in text
    assert "approvals:" in json.dumps(sec["citations"])


def test_ac7_validation_section_states_absence_and_cites_agent(client):
    aid = _agent(client)
    assert _gen(client, aid).status_code == 200
    sec = _section(_pack(client, aid), "validation")
    assert "no validation record" in json.dumps(sec["content"]).lower()
    assert f"agents:{aid}" in json.dumps(sec["citations"])


def test_ac7_attaches_to_governance_bundle(client, fake):
    aid = _agent(client, "domino-deployed-agent")
    bid = client.post(f"/api/agents/{aid}/governance", headers=ALICE).json()["bundle_id"]
    n = len(fake.calls)
    r = _gen(client, aid)
    assert r.status_code == 200, r.text
    assert r.json()["bundle_id"] == bid
    new = fake.calls[n:]
    assert any(m in ("POST", "PUT", "PATCH") and bid in u for m, u in new), new


def test_ac7_no_bundle_falls_back_to_dataset_note(client, evdir):
    aid = _agent(client)
    r = _gen(client, aid)
    assert r.status_code == 200
    assert r.json()["bundle_id"] is None
    assert Path(r.json()["pdf_path"]).is_file()
    text = json.dumps(_section(_pack(client, aid), "records_retention")["content"])
    assert "dataset" in text.lower() or str(evdir) in text


def test_ac7_anonymous_401_unknown_404(client):
    aid = _agent(client)
    assert _gen(client, aid).status_code == 200  # route exists for a known agent
    assert client.post(f"/api/agents/{aid}/evidence-pack").status_code == 401
    assert client.post("/api/agents/nope-nope/evidence-pack", headers=ALICE).status_code == 404


def test_ac7_pack_has_no_claude_string(client):
    aid = _agent(client)
    _ingest_drift(client)
    b = _gen(client, aid).json()
    assert "claude" not in Path(b["markdown_path"]).read_text().lower()
    assert "claude" not in json.dumps(_pack(client, aid)).lower()


def test_ac7_audit_event_evidence_pack_generated(client):
    aid = _agent(client)
    assert _gen(client, aid).status_code == 200
    from backend import db
    with db.engine.connect() as cx:
        rows = [r for r in cx.execute(db.audit_events.select()).fetchall() if r.action == "evidence_pack_generated"]
    assert len(rows) == 1
    assert rows[0].actor_user_id == "alice-id" and rows[0].actor_username == "alice"
