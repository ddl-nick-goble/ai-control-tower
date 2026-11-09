"""T-12 tests: external A2A agent intake and passport (AC-11).

Assumed names: POST /api/agents/external {card_url, criteria}; env ACT_TRUSTED_JWKS_PATH
(path to a JWK or JWK set file of trusted signing keys); audit action "external_registered".
"""
import copy
import datetime as dt
import http.server
import importlib
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "fixtures" / "external"
SCHEMA = json.loads((ROOT / "schemas" / "agent-record.json").read_text())
HDR = {"X-Domino-User-Id": "u12", "X-Domino-User": "ext-tester"}
CRITERIA_NAMES = ["decision_impact", "financial_exposure", "customer_impact", "autonomy",
                  "data_sensitivity", "reversibility", "regulatory_exposure"]
HIGH = {k: 5 for k in CRITERIA_NAMES}
LOW = {k: 1 for k in CRITERIA_NAMES}
WK = "/.well-known/agent-card.json"


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(port):
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture
def fixture_server():
    procs = []

    def start(signed=False):
        port = _port()
        cmd = [sys.executable, str(EXT / "serve.py"), "--port", str(port)] + (["--signed"] if signed else [])
        procs.append(subprocess.Popen(cmd))
        _wait(port)
        return f"http://127.0.0.1:{port}{WK}"
    yield start
    for p in procs:
        p.kill()
        p.wait()


@pytest.fixture
def card_server():
    servers = []

    def start(body: bytes, delay=0.0, ctype="application/json"):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(delay)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}{WK}"
    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


@pytest.fixture
def env(db_env, monkeypatch):
    monkeypatch.setenv("ACT_TRUSTED_JWKS_PATH", str(EXT / "signing-key.pub.jwk.json"))
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    return db_env


@pytest.fixture
def client(env):
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


def _register(client, url, criteria=None, headers=HDR):
    body = {"card_url": url}
    if criteria is not None:
        body["criteria"] = criteria
    return client.post("/api/agents/external", json=body, headers=headers)


def _create(client, url, criteria=None):
    r = _register(client, url, HIGH if criteria is None else criteria)
    assert r.status_code in (200, 201), r.text
    rec = r.json()
    return client.get(f"/api/agents/{rec['id']}").json()


def _card(**over):
    c = json.loads((EXT / "agent-card.json").read_text())
    c.update(over)
    return c


def test_ac11_unsigned_card_registers_pending_review(client, fixture_server):
    url = fixture_server()
    r = _register(client, url, HIGH)
    assert r.status_code in (200, 201), r.text
    rec = client.get(f"/api/agents/{r.json()['id']}").json()
    assert rec["origin"] == "external"
    assert rec["card_signature"] == "unsigned"
    assert rec["status"] == "pending_review"
    assert rec["registered_by"] == HDR["X-Domino-User-Id"]
    assert rec["tier"] in (1, 2, 3, 4)
    assert rec["tier_reasoning"]
    jsonschema.validate(rec, SCHEMA)


def test_ac11_signed_card_verified_with_trusted_key(client, fixture_server):
    rec = _create(client, fixture_server(signed=True))
    assert rec["card_signature"] == "signed"
    assert rec["status"] == "pending_review"


def test_ac11_tampered_signed_card_recorded_invalid(client, card_server):
    card = json.loads((EXT / "agent-card-signed.json").read_text())
    card["description"] += " (tampered)"
    url = card_server(json.dumps(card).encode())
    r = _register(client, url, HIGH)
    assert r.status_code in (200, 201), r.text
    rec = client.get(f"/api/agents/{r.json()['id']}").json()
    assert rec["card_signature"] == "invalid"
    assert rec["status"] == "pending_review"


def test_ac11_passport_view(client, fixture_server):
    rec = _create(client, fixture_server())
    p = rec["passport"]
    assert "Meridian" in p["identity"] or "Treasury" in p["identity"]
    assert set(p["allowed_skills"]) == {"request-balance", "initiate-payment", "reconcile-statement"}
    assert isinstance(p["data_scopes"], list) and p["data_scopes"]
    exp = dt.datetime.fromisoformat(p["expires_at"].replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    assert exp > dt.datetime.now(dt.timezone.utc)


def test_ac11_tier_from_criteria_differs(client, card_server):
    """Benign card (no money-movement tags): low vs high answers yield different tiers."""
    card = _card(skills=[{"id": "summarise", "name": "Summarise", "description": "d", "tags": ["text"]}])
    u1 = card_server(json.dumps(card).encode())
    u2 = card_server(json.dumps(dict(card, name="Other Bot")).encode())
    low = _create(client, u1, LOW)
    high = _create(client, u2, HIGH)
    assert low["tier"] > high["tier"]  # tier 1 is highest consequence


def test_ac11_money_movement_skill_floor_tier2(client, card_server):
    """Rule asserted (money_movement_floor): a card with a skill tagged payments/transfer
    is never tiered below Tier 2 (tier number must be 1 or 2) whatever the answers."""
    card = _card(skills=[{"id": "pay", "name": "Pay", "description": "d", "tags": ["transfer"]}])
    rec = _create(client, card_server(json.dumps(card).encode()), LOW)
    assert rec["tier"] in (1, 2)


def test_ac11_benchmark_criteria_rejected(client, fixture_server):
    crit = dict(HIGH, backtest_sharpe=3)
    r = _register(client, fixture_server(), crit)
    assert r.status_code == 422
    assert "no_benchmark_rule" in r.text


def test_ac11_timeout_returns_504(client, card_server):
    url = card_server(json.dumps(_card()).encode(), delay=7)
    t = time.monotonic()
    r = _register(client, url, HIGH)
    assert r.status_code == 504
    assert time.monotonic() - t < 6.5


def test_ac11_unreachable_url_502(client):
    r = _register(client, f"http://127.0.0.1:{_port()}{WK}", HIGH)
    assert r.status_code == 502


def test_ac11_non_json_body_422(client, card_server):
    r = _register(client, card_server(b"<html>not json</html>", ctype="text/html"), HIGH)
    assert r.status_code == 422


def test_ac11_missing_card_url_422(client):
    r = client.post("/api/agents/external", json={"criteria": HIGH}, headers=HDR)
    assert r.status_code == 422


def test_ac11_anonymous_401(client, fixture_server):
    r = _register(client, fixture_server(), HIGH, headers={})
    assert r.status_code == 401


def test_ac11_file_scheme_rejected(client):
    r = _register(client, "file:///etc/passwd", HIGH)
    assert r.status_code == 422
    assert "root:" not in r.text


def test_ac11_reregister_no_duplicate(client, fixture_server):
    url = fixture_server()
    r1 = _register(client, url, HIGH)
    assert r1.status_code in (200, 201)
    r2 = _register(client, url, HIGH)
    assert r2.status_code in (200, 201, 409)
    if r2.status_code != 409:
        assert r2.json()["id"] == r1.json()["id"]
    ext = [a for a in client.get("/api/agents").json() if a.get("origin") == "external"]
    assert len(ext) == 1


def test_ac11_audit_event_external_registered(client, fixture_server):
    r = _register(client, fixture_server(), HIGH)
    assert r.status_code in (200, 201)
    db = importlib.import_module("backend.db")
    with db.engine.begin() as conn:
        rows = conn.execute(select(db.audit_events).where(
            db.audit_events.c.action == "external_registered")).fetchall()
    assert rows
    assert rows[0].actor_user_id == HDR["X-Domino-User-Id"]
