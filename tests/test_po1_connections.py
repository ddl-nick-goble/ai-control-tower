"""PO-1: Connections core (/api/connections). Written from the AC and interface contract only.

Interface contract the builder follows:
- POST /api/connections {provider, sample?} -> {id, provider, state, sample, artifact: {kind, text, open_url?}, state_token}
  artifact.kind in quick_create_url | cloud_shell | cli_script | sql_worksheet; artifact.text contains the Domino
  issuer, "domino-platform", the act-svc subject and the state token.
- GET /api/connections -> list (or {"connections": [...]}); GET /api/connections/{id} -> {id, provider, state, sample,
  scopes, last_check, last_scan, agent_count}. DELETE /api/connections/{id} -> state "disconnected".
- GET /api/connections/callback?state=... -> 200 with a plain-language message; unknown/used state is a 200 with
  {"ok": false, "message": "..."} style body (no 4xx/5xx). Success moves the connection to trusted.
- POST /api/connections/{id}/paste {text} -> {recognised: bool, kind, value, message}; kind is one of aws_role_arn,
  gcp_project, gcp_wif_provider, azure_guid, databricks_account, databricks_url, databricks_code, snowflake_url,
  snowflake_account, a2a_url, unknown.
- POST /api/connections/{id}/check -> {state, permissions: [{name, ok, fix}]}
- GET/PUT /api/connections/{id}/scopes -> {tree: [{id, label, selected, children}]}; PUT body {"selected": [ids]}.
- Sample-mode auto-trust delay: env ACT_SAMPLE_TRUST_DELAY_S (default ~3). Tests set it to 0 (instant) or large
  (never). Detection happens on GET polling of /api/connections/{id}. Builder may also expose
  backend.connections.store.now as an injectable clock.
- Audit: rows in db.audit_events with the Domino user; action contains "connect", "disconnect" or "paste".
"""
import importlib
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

H = {"X-Domino-User-Id": "u-owner", "X-Domino-User": "owner"}
ISSUER = "https://se-demo.domino.tech/auth/realms/DominoRealm"
PROVIDERS = ["aws", "google", "microsoft", "databricks", "snowflake"]
KINDS = {"aws": "quick_create_url", "google": "cloud_shell", "microsoft": "cloud_shell",
         "databricks": "cli_script", "snowflake": "sql_worksheet"}


def _purge():
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]


def _make(db_env, tmp_path, monkeypatch, delay):
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "owner")
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("ACT_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")
    monkeypatch.setenv("ACT_SAMPLE_TRUST_DELAY_S", str(delay))
    _purge()
    return importlib.import_module("backend.main")


@pytest.fixture
def client(db_env, tmp_path, monkeypatch):
    main = _make(db_env, tmp_path, monkeypatch, 0)
    with TestClient(main.app) as c:
        yield c
    _purge()


@pytest.fixture
def slow_client(db_env, tmp_path, monkeypatch):
    main = _make(db_env, tmp_path, monkeypatch, 100000)
    with TestClient(main.app) as c:
        yield c
    _purge()


def create(c, provider, sample=False):
    r = c.post("/api/connections", json={"provider": provider, "sample": sample}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()


def get(c, cid):
    r = c.get(f"/api/connections/{cid}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _items(body):
    return body["connections"] if isinstance(body, dict) else body


# ---- PO-1.1 ----
@pytest.mark.parametrize("provider", PROVIDERS)
def test_ac1_create_returns_artifact(client, provider):
    d = create(client, provider)
    assert d["provider"] == provider and d["state"] == "draft" and d["sample"] is False
    assert d["id"] and d["state_token"]
    a = d["artifact"]
    assert a["kind"] == KINDS[provider]
    for needle in (ISSUER, "domino-platform", d["state_token"], "act-svc"):
        assert needle in a["text"], needle


def test_ac1_aws_quick_create_url_and_template(client):
    a = create(client, "aws")["artifact"]
    assert a["open_url"].startswith("https://") and "cloudformation" in a["open_url"].lower()
    assert "AWS::IAM::Role" in a["text"] and "sts:AssumeRoleWithWebIdentity" in a["text"]


def test_ac1_provider_specific_text(client):
    assert "gcloud" in create(client, "google")["artifact"]["text"]
    assert "az " in create(client, "microsoft")["artifact"]["text"]
    assert "databricks" in create(client, "databricks")["artifact"]["text"].lower()
    sql = create(client, "snowflake")["artifact"]["text"]
    assert "SELECT" in sql.upper()


def test_ac1_return_url_in_artifact(client):
    d = create(client, "google")
    assert "callback" in d["artifact"]["text"] or "finish" in d["artifact"]["text"].lower()


def test_ac1_rejects_unknown_provider(client):
    r = client.post("/api/connections", json={"provider": "oracle"}, headers=H)
    assert r.status_code in (400, 422)


# ---- PO-1.2 ----
def test_ac2_list_and_get_fields(client):
    d = create(client, "aws", sample=True)
    items = _items(client.get("/api/connections", headers=H).json())
    assert any(i["id"] == d["id"] for i in items)
    g = get(client, d["id"])
    for k in ("state", "provider", "sample", "scopes", "last_check", "last_scan", "agent_count"):
        assert k in g, k
    assert g["provider"] == "aws" and g["sample"] is True and g["agent_count"] == 0


def test_ac2_get_unknown_is_404(client):
    create(client, "aws")  # route must exist for real ids
    assert client.get("/api/connections/nope-123", headers=H).status_code == 404


def test_ac2_delete_marks_disconnected_and_keeps_record(client):
    d = create(client, "aws", sample=True)
    r = client.delete(f"/api/connections/{d['id']}", headers=H)
    assert r.status_code in (200, 204)
    assert get(client, d["id"])["state"] == "disconnected"


# ---- PO-1.3 ----
def test_ac3_callback_moves_to_trusted(slow_client):
    c = slow_client
    d = create(c, "google")
    r = c.get("/api/connections/callback", params={"state": d["state_token"]})
    assert r.status_code == 200
    assert get(c, d["id"])["state"] == "trusted"


def test_ac3_callback_unknown_state_is_plain_language(client):
    r = client.get("/api/connections/callback", params={"state": "bogus"})
    assert r.status_code == 200
    assert len(r.text) > 10 and "Traceback" not in r.text


def test_ac3_callback_state_single_use(client):
    d = create(client, "google")
    client.get("/api/connections/callback", params={"state": d["state_token"]})
    r = client.get("/api/connections/callback", params={"state": d["state_token"]})
    assert r.status_code == 200
    body = r.text.lower()
    assert "already" in body or "expired" in body or "not" in body or "used" in body


# ---- PO-1.5 ----
@pytest.mark.parametrize("provider", PROVIDERS)
def test_ac5_sample_flow_auto_trusts_by_polling(client, provider):
    d = create(client, provider, sample=True)
    assert d["sample"] is True
    assert d["state"] in ("draft", "waiting")
    g = get(client, d["id"])
    assert g["sample"] is True and g["state"] in ("waiting", "trusted")
    assert get(client, d["id"])["state"] == "trusted"


def test_ac5_sample_waits_before_delay(slow_client):
    d = create(slow_client, "aws", sample=True)
    assert get(slow_client, d["id"])["state"] == "waiting"
    assert get(slow_client, d["id"])["state"] == "waiting"


def test_ac5_check_has_one_missing_then_recheck_green(client):
    d = create(client, "aws", sample=True)
    get(client, d["id"]); get(client, d["id"])
    r = client.post(f"/api/connections/{d['id']}/check", headers=H).json()
    missing = [p for p in r["permissions"] if not p["ok"]]
    assert len(missing) == 1 and missing[0]["fix"] and len(r["permissions"]) > 1
    assert r["state"] == "partial"
    r2 = client.post(f"/api/connections/{d['id']}/check", headers=H).json()
    assert all(p["ok"] for p in r2["permissions"])
    assert r2["state"] == "verified"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_ac5_sample_scopes_and_flag_in_list(client, provider):
    d = create(client, provider, sample=True)
    tree = client.get(f"/api/connections/{d['id']}/scopes", headers=H).json()["tree"]
    assert tree and all(n["selected"] for n in tree)
    items = _items(client.get("/api/connections", headers=H).json())
    assert [i for i in items if i["id"] == d["id"]][0]["sample"] is True


# ---- PO-1.6 ----
def test_ac6_scopes_default_selected_and_put_saves(client):
    d = create(client, "snowflake", sample=True)
    get(client, d["id"]); get(client, d["id"])
    tree = client.get(f"/api/connections/{d['id']}/scopes", headers=H).json()["tree"]
    for n in tree:
        assert {"id", "label", "selected", "children"} <= set(n)
    flat = []
    def walk(ns):
        for n in ns:
            flat.append(n); walk(n["children"])
    walk(tree)
    assert all(n["selected"] for n in flat)
    keep = [flat[0]["id"]]
    r = client.put(f"/api/connections/{d['id']}/scopes", json={"selected": keep}, headers=H)
    assert r.status_code == 200, r.text
    tree2 = client.get(f"/api/connections/{d['id']}/scopes", headers=H).json()["tree"]
    flat2 = []
    def walk2(ns):
        for n in ns:
            flat2.append(n); walk2(n["children"])
    walk2(tree2)
    sel = {n["id"] for n in flat2 if n["selected"]}
    assert flat[0]["id"] in sel and len(sel) < len(flat2)


def test_ac6_check_shape(client):
    d = create(client, "google", sample=True)
    get(client, d["id"]); get(client, d["id"])
    r = client.post(f"/api/connections/{d['id']}/check", headers=H).json()
    assert r["state"] in ("verified", "partial")
    for p in r["permissions"]:
        assert {"name", "ok", "fix"} <= set(p)
    assert get(client, d["id"])["state"] == r["state"]
    assert get(client, d["id"])["last_check"]


# ---- PO-1.8 ----
def _audit(db_env):
    cx = sqlite3.connect(str(db_env))
    try:
        return cx.execute("select actor_user_id, actor_username, action, target from audit_events").fetchall()
    finally:
        cx.close()


def test_ac8_connect_paste_disconnect_audited(client, db_env):
    d = create(client, "aws")
    client.post(f"/api/connections/{d['id']}/paste",
                json={"text": "arn:aws:iam::123456789012:role/ActReadOnly"}, headers=H)
    client.delete(f"/api/connections/{d['id']}", headers=H)
    rows = [r for r in _audit(db_env) if "connect" in (r[2] or "").lower() or "paste" in (r[2] or "").lower()]
    actions = " ".join(r[2].lower() for r in rows)
    assert "paste" in actions and "disconnect" in actions
    assert len(rows) >= 3
    assert all(r[0] == "u-owner" for r in rows)
