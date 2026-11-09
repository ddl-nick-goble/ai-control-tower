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


# ---------------- round 2: honesty, inertness, counts, orphans ----------------

EVIL = "evil ![x](http://169.254.169.254/latest) [y](file:///etc/passwd) <img src=x>"
EVIL_MSG = "bad thing ![z](http://127.0.0.1:1/) happened"


def _seed_agent(aid="act:test:evil0001", name="plain-agent"):
    from backend import db
    with db.engine.begin() as cx:
        cx.execute(db.agents.insert().values(
            id=aid, source_platform="aws", native_id=aid, name=name, owner="o", tier="2", status="pending_review",
            record={}, criteria={}, tier_reasoning="r"))
    return aid


def _seed_finding(aid, **kw):
    from backend import db
    vals = dict(agent_id=aid, run_id="r-seed", severity="high", rule="scope_drift", message="m", status="open",
                created_at="2026-11-07T02:14:00Z", detail={}, type="scope_drift", ts="2026-11-07T02:14:00Z")
    vals.update(kw)
    with db.engine.begin() as cx:
        return cx.execute(db.findings.insert().values(**vals)).inserted_primary_key[0]


def _records_text(client, aid):
    return json.dumps(_section(_pack(client, aid), "records_retention")["content"])


@pytest.fixture
def failing_gov(monkeypatch):
    """Fake Governance API whose submit-result fails with 500; records every POST body."""
    posts = []
    orig = FakeDomino.__call__

    def call(self, *a, **k):
        url = next((x for x in a if isinstance(x, str) and x.startswith("http")), "")
        if "/api/governance/v1" in url and k.get("json") is not None:
            posts.append((url, k["json"]))
        if "submit-result" in url:
            from test_t8_governance import _Resp
            return _Resp({"error": "boom"}, 500)
        return orig(self, *a, **k)
    monkeypatch.setattr(FakeDomino, "__call__", call)
    return posts


def _sources(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "source":
                yield v
            yield from _sources(v)
    elif isinstance(o, list):
        for x in o:
            yield from _sources(x)


def _bundled(client):
    aid = _agent(client, "domino-deployed-agent")
    bid = client.post(f"/api/agents/{aid}/governance", headers=ALICE).json()["bundle_id"]
    return aid, bid


def test_ac7_no_fake_attachment_when_submit_fails(client, failing_gov):
    aid, bid = _bundled(client)
    n = len(failing_gov)
    r = _gen(client, aid)
    assert r.status_code == 200, r.text
    b = r.json()
    for url, body in failing_gov[n:]:
        if "attachments" in url:
            assert all(s == "git" for s in _sources(body)), (url, body)
    assert "attach_failed" in str(b.get("governance")), b.get("governance")
    assert Path(b["markdown_path"]).is_file() and Path(b["pdf_path"]).is_file()


def test_ac7_honest_wording_and_audit_when_attach_fails(client, failing_gov):
    aid, bid = _bundled(client)
    b = _gen(client, aid).json()
    text = _records_text(client, aid)
    md = Path(b["markdown_path"]).read_text()
    for t in (text, md):
        assert not re.search(r"\battached\b", t, re.I), t
    assert "act/evidence/" in text and safe_id_of(aid) in text
    from backend import db
    with db.engine.connect() as cx:
        acts = [r.action for r in cx.execute(db.audit_events.select()).fetchall()]
    assert "evidence_pack_attach_failed" in acts
    assert "evidence_pack_attached" not in acts


def safe_id_of(aid):
    return re.sub(r"[^A-Za-z0-9]+", "-", aid).strip("-")


def test_ac7_no_bundle_wording_names_dataset_location(client):
    aid = _agent(client)
    assert _gen(client, aid).status_code == 200
    text = _records_text(client, aid)
    assert not re.search(r"\battached\b", text, re.I), text
    assert "act/evidence/" in text and safe_id_of(aid) in text


def test_ac7_untrusted_text_is_inert(client, monkeypatch):
    import weasyprint
    aid = _seed_agent(name=EVIL)
    _seed_finding(aid, message=EVIL_MSG, detail={"explanation": EVIL_MSG})
    fetched, htmls = [], []
    orig_html = weasyprint.HTML

    def fetcher(url, *a, **k):
        fetched.append(url)
        raise ValueError("fetch blocked by test")

    class H(orig_html):
        def __init__(self, *a, **k):
            if k.get("string") is not None:
                htmls.append(k["string"])
            k.setdefault("url_fetcher", fetcher)
            super().__init__(*a, **k)
    if hasattr(weasyprint, "default_url_fetcher"):
        monkeypatch.setattr(weasyprint, "default_url_fetcher", fetcher)
    monkeypatch.setattr(weasyprint, "HTML", H)
    r = _gen(client, aid)
    assert r.status_code == 200, r.text
    assert fetched == []
    for h in htmls:
        assert "<img" not in h.lower() and 'href="file:' not in h.lower() and "href='file:" not in h.lower(), h
    md = Path(r.json()["markdown_path"]).read_text()
    assert "![" not in md
    assert "](http" not in md and "](file:" not in md
    assert "<img" not in md.lower()


def test_ac12_pack_event_and_run_counts_match_db(client):
    aid = _agent(client)
    _ingest_drift(client)
    from backend import db
    from sqlalchemy import func, select
    with db.engine.connect() as cx:
        n_ev = cx.execute(select(func.count()).select_from(db.events).where(db.events.c.agent_id == aid)).scalar()
        n_runs = cx.execute(select(func.count()).select_from(db.runs).where(db.runs.c.agent_id == aid)).scalar()
    assert n_runs == 2
    assert _gen(client, aid).status_code == 200
    text = _records_text(client, aid)
    assert re.search(rf"Events for this agent: {n_ev}\b", text), (n_ev, text)
    assert re.search(r"\b2 runs\b", text), text


def test_ac12_missing_values_omitted_and_present_values_shown(client):
    aid = _seed_agent()
    _seed_finding(aid, run_id="r1", reason="tool_not_in_manifest", tool="list_credentials",
                  observed_value=None, limit=None,
                  detail={"violations": [{"tool": "list_credentials", "reason": "tool_not_in_manifest",
                                          "observed_value": None, "limit": None}]})
    _seed_finding(aid, run_id="r2", type="value_limit", reason="value_limit_exceeded", tool="transfer_funds",
                  observed_value="7500", limit="5000",
                  detail={"violations": [{"tool": "transfer_funds", "reason": "value_limit_exceeded",
                                          "observed_value": 7500, "limit": 5000}]})
    md = Path(_gen(client, aid).json()["markdown_path"]).read_text()
    lines = [ln for ln in md.splitlines() if "reason tool_not_in_manifest" in ln or "tool_not_in_manifest" in ln]
    assert lines
    for ln in lines:
        assert not re.search(r"\b(observed|limit)\b", ln, re.I), ln
    vl = [ln for ln in md.splitlines() if "value_limit_exceeded" in ln]
    assert vl
    assert any("7500" in ln and "5000" in ln for ln in vl), vl


def test_ac7_pack_cites_model_dependency(client):
    aid = _agent(client)
    from backend import db
    with db.engine.begin() as cx:
        did = cx.execute(db.dependencies.insert().values(
            agent_id=aid, kind="model", name="break_classifier", target="2",
            record={"name": "break_classifier"})).inserted_primary_key[0]
    assert _gen(client, aid).status_code == 200
    pack = _pack(client, aid)
    cites = json.dumps([_section(pack, s)["citations"] for s in
                        ("identity_ownership", "tier_criteria", "permitted_actions")])
    assert f"dependencies:{did}" in cites


def test_ac7_runs_by_agent_endpoint_lists_all_runs_newest_first(client):
    aid = _agent(client)
    _ingest_drift(client)
    r = client.get(f"/api/agents/{aid}/runs")
    assert r.status_code == 200, r.text
    runs = r.json()
    assert len(runs) == 2
    for x in runs:
        assert x.get("run_id") and x.get("started_at")
        assert any("span" in k and isinstance(v, int) for k, v in x.items()), x
    assert runs[0]["started_at"] >= runs[1]["started_at"]


def test_ac7_render_failure_leaves_no_orphan_md_and_returns_500(client, evdir, monkeypatch):
    aid = _agent(client)
    ev = importlib.import_module("backend.evidence")

    def boom(*a, **k):
        raise RuntimeError("renderer exploded")
    monkeypatch.setattr(ev, "render_pdf", boom)
    monkeypatch.setattr(ev, "_pdf_weasy", boom, raising=False)
    monkeypatch.setattr(ev, "_pdf_reportlab", boom, raising=False)
    main = importlib.import_module("backend.main")
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.post(f"/api/agents/{aid}/evidence-pack", headers=ALICE)
    assert r.status_code == 500
    assert r.text.strip() and r.text.strip() != "Internal Server Error", r.text
    assert list(evdir.rglob("*.md")) == [] if evdir.exists() else True
    from backend import db
    with db.engine.connect() as cx:
        assert cx.execute(db.evidence_packs.select()).first() is None
