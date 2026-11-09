"""T-14 tests: local hosting and mocks behind env switches (honest labels).

Assumed contracts (builder must match):
- ACT_LLM_PROVIDER=mock|gateway (unset = gateway), read at call time, in both
  agents.recon_agent.llm.create_message and backend.llm.create_message (same signature).
  Mock returns Anthropic-style dicts with "model": "mock-llm" and makes no network calls.
- Mock judge: jobs.validate.default_judge -> {"score": 0..1, "model": "mock-llm", ...}.
- ACT_DOMINO_LOCAL_AGENTS=<json file>; repo ships config/local_agents.json (recon-agent,
  native id "local-recon-agent"). Record text contains "workspace (not deployed)".
- jobs.validate.main() with ACT_URL unset writes validation_records in-process.
- Drift finding detail carries explanation and explanation_model ("mock-llm").
- docs/INTEGRATION.md names each switch.
"""
import contextlib
import hashlib
import importlib
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_t10_evidence import fake, evdir, client as ev_client, _agent, _pack, _section, ALICE  # noqa: F401
from test_t4_intake import env as t4_env  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
LOCAL_NATIVE = "local-recon-agent"
LOCAL_ACT_ID = "act:domino:" + hashlib.sha256(LOCAL_NATIVE.encode()).hexdigest()[:16]
DRIFT = json.loads((ROOT / "fixtures" / "runs" / "weekend-drift.json").read_text())
HDR = {"X-Domino-User-Id": "u1", "X-Domino-User": "tester"}
BREAK_REFS = ["TXN-003", "TXN-005", "TXN-007", "TXN-010", "TXN-012", "TXN-014"]


@pytest.fixture(autouse=True)
def _no_mlflow_agent_context(monkeypatch, tmp_path):
    """MLflow/Domino SDK is an external service not covered by the ACs: keep it out of the way."""
    import tracing_setup
    monkeypatch.setattr(tracing_setup, "DominoAgentContext", None, raising=False)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")


def _mod(name):
    return importlib.import_module(name)


def _no_network(monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("network call made in mock mode")
    for n in ("get", "post", "request", "put", "delete"):
        monkeypatch.setattr(requests, n, boom)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: boom())
    monkeypatch.setattr(requests.Session, "send", lambda *a, **k: boom())
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: boom())


def _is_anthropic_style(d):
    assert isinstance(d, dict)
    assert "stop_reason" in d and isinstance(d["content"], list)
    for b in d["content"]:
        assert b["type"] in ("text", "tool_use")
    return d


# ---------------------------------------------------------------- 1. LLM seam
@pytest.mark.parametrize("modname", ["agents.recon_agent.llm", "backend.llm"])
def test_ac1_mock_provider_no_network_and_mock_model(modname, monkeypatch):
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    m = _mod(modname)
    _no_network(monkeypatch)
    r = m.create_message("haiku", [{"role": "user", "content": "Explain this drift finding."}],
                         system="s", max_tokens=100, tags={"purpose": "x"})
    _is_anthropic_style(r)
    assert r["model"] == "mock-llm"
    r2 = m.create_message("sonnet", [{"role": "user", "content": "hi"}], tools=[
        {"name": "fetch_ledger", "description": "d", "input_schema": {"type": "object"}}])
    _is_anthropic_style(r2)
    assert r2["model"] == "mock-llm"


@pytest.mark.parametrize("modname", ["agents.recon_agent.llm", "backend.llm"])
@pytest.mark.parametrize("provider", [None, "gateway"])
def test_ac1_gateway_path_used_when_unset_or_gateway(modname, provider, monkeypatch):
    import requests
    if provider is None:
        monkeypatch.delenv("ACT_LLM_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("ACT_LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://gw.invalid/apps/x")
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "tok")
    monkeypatch.setenv("DOMINO_API_PROXY", "http://127.0.0.1:1")
    posts = []

    class R:
        status_code = 200
        headers = {}
        text = "tok"

        def json(self):
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "gw"}],
                    "model": "claude-real", "usage": {}, "id": "m1"}
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(requests, "post", lambda url, *a, **k: posts.append(url) or R())
    m = _mod(modname)
    out = m.create_message("sonnet", [{"role": "user", "content": "hi"}])
    assert posts and posts[0].startswith("http://gw.invalid/apps/x")
    assert out["model"] == "claude-real"


def test_ac1_same_signature_both_modes():
    import inspect
    a = inspect.signature(_mod("agents.recon_agent.llm").create_message)
    b = inspect.signature(_mod("backend.llm").create_message)
    assert list(a.parameters) == list(b.parameters)


# ---------------------------------------------------------------- shared helpers
@pytest.fixture
def trained_model(tmp_path, monkeypatch):
    train = _mod("models.break_classifier.train")
    train.train(str(tmp_path))
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    path = next((p for p in files if p.suffix in (".pkl", ".joblib")), files[0])
    monkeypatch.setenv("BREAK_CLASSIFIER_MODEL_PATH", str(path))
    return path


class FakeMlflow:
    def __init__(self):
        self.runs, self._cur = [], None

    def set_experiment(self, name):
        pass

    @contextlib.contextmanager
    def start_run(self, run_name=None, **kw):
        run = {"id": f"run{len(self.runs)}", "params": {}, "metrics": []}
        self.runs.append(run)
        self._cur = run
        yield type("R", (), {"info": type("I", (), {"run_id": run["id"]})()})()

    def log_param(self, k, v):
        self._cur["params"][k] = v

    def log_params(self, d):
        pass

    def log_metric(self, k, v, step=None):
        self._cur["metrics"].append((k, v))

    def log_metrics(self, d, step=None):
        pass

    def set_tag(self, *a, **k):
        pass

    def log_dict(self, *a, **k):
        pass

    def log_text(self, *a, **k):
        pass


def _attrs(span):
    out = {}
    for a in span.get("attributes", []):
        v = a["value"]
        out[a["key"]] = next(iter(v.values())) if v else None
    return out


# ---------------------------------------------------------------- 2. recon agent in mock mode
def test_ac2_recon_agent_runs_in_mock_mode(trained_model, monkeypatch):
    posts = []

    class ActH(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            posts.append(self.rfile.read(n))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    class TokH(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"tok")

        def log_message(self, *a):
            pass
    act = HTTPServer(("127.0.0.1", 0), ActH)
    tok = HTTPServer(("127.0.0.1", 0), TokH)
    for s in (act, tok):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    monkeypatch.setenv("ACT_TRACES_URL", f"http://127.0.0.1:{act.server_port}/v1/traces")
    monkeypatch.setenv("ACT_TOKEN_URL", f"http://127.0.0.1:{tok.server_port}/access-token")
    monkeypatch.setenv("DOMINO_API_PROXY", "")
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("RECON_AGENT_ID", LOCAL_NATIVE)
    monkeypatch.delenv("BREAK_CLASSIFIER_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    agent = _mod("agents.recon_agent.agent")
    try:
        result = agent.run("Reconcile the GL and custodian ledgers")
    finally:
        act.shutdown()
        tok.shutdown()
    assert isinstance(result, dict)
    answer = json.dumps(result.get("summary") or result.get("output") or "")
    assert (result.get("summary") or "").strip(), result
    assert any(r in answer for r in BREAK_REFS), answer
    tools = set()
    for body in posts:
        for rs in json.loads(body)["resourceSpans"]:
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    a = _attrs(sp)
                    if a.get("gen_ai.operation.name") == "execute_tool":
                        tools.add(a.get("gen_ai.tool.name"))
    assert {"fetch_ledger", "compare_entries", "break_classifier", "propose_adjustment"} <= tools


# ---------------------------------------------------------------- 3. mock judge
def test_ac3_mock_judge_scores_and_orders(monkeypatch):
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    v = _mod("jobs.validate")
    _no_network(monkeypatch)
    exp = "Amount mismatch TXN-007 with difference 250.00."
    good = v.default_judge("Which break is largest?", {"summary": "TXN-007 differs by 250.00"}, exp)
    bad = v.default_judge("Which break is largest?", {"summary": "Everything matches, nothing to do."}, exp)
    for j in (good, bad):
        assert 0.0 <= j["score"] <= 1.0
        assert j["model"] == "mock-llm"
    assert good["score"] > bad["score"]


def _mock_validation(monkeypatch, reps=5):
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "projx")
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "alice")
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("RECON_AGENT_ID", LOCAL_NATIVE)
    monkeypatch.delenv("ACT_TRACES_URL", raising=False)
    monkeypatch.delenv("BREAK_CLASSIFIER_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    v = _mod("jobs.validate")
    mlf = FakeMlflow()
    res = v.run_validation(v.load_prompts(), reps=reps, mlflow_client=mlf)
    return res


def test_ac3_run_validation_default_agent_and_judge_mock(trained_model, monkeypatch):
    res = _mock_validation(monkeypatch)
    total = res["n_prompts"] * res["reps"]
    assert res["reps"] == 5 and total >= 100
    assert (res["error_count"] + res["invalid_count"]) / total <= 0.2
    assert res["judge_model_id"] == "mock-llm"
    assert len(res["scores"]) == total


# ---------------------------------------------------------------- 4. honest labels
def test_ac4_evidence_labels_mock_judge(trained_model, monkeypatch, ev_client):
    res = _mock_validation(monkeypatch)
    aid = _agent(ev_client)
    r = ev_client.post(f"/api/agents/{aid}/validation", json=res, headers=ALICE)
    assert r.status_code in (200, 201), r.text
    g = ev_client.get(f"/api/agents/{aid}/validation")
    assert g.status_code == 200 and g.json()["detail"]["judge_model_id"] == "mock-llm"
    assert ev_client.post(f"/api/agents/{aid}/evidence-pack", headers=ALICE).status_code == 200
    text = json.dumps(_section(_pack(ev_client, aid), "validation")["content"]).lower()
    assert "mock judge" in text and "not a model" in text
    assert "claude" not in text and "sonnet" not in text


# ---------------------------------------------------------------- 5. drift explanation
def test_ac5_drift_explanation_mock_is_template_not_model(t4_env, monkeypatch):
    """Round 2 contract 1: nothing mocked is presented as model output."""
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters", "act_llm_mock")]:
        del sys.modules[m]
    main = _mod("backend.main")
    calls = []
    llm = _mod("backend.llm")
    real = llm.create_message
    monkeypatch.setattr(llm, "create_message", lambda *a, **k: calls.append(a) or real(*a, **k))
    mock = _mod("act_llm_mock")
    for name, fn in list(vars(mock).items()):
        if callable(fn) and not name.startswith("_") and getattr(fn, "__module__", None) == "act_llm_mock":
            monkeypatch.setattr(mock, name, (lambda f, n: lambda *a, **k: calls.append(n) or f(*a, **k))(fn, name))
    with TestClient(main.app) as c:
        _mod("jobs.intake").run_intake()
        aid = next(a for a in c.get("/api/agents").json() if a["name"] == "treasury-sweep-agent")["id"]
        assert c.post("/v1/traces", json=DRIFT, headers=HDR).status_code == 200
        deadline = time.monotonic() + 10
        f = None
        while time.monotonic() < deadline and f is None:
            fs = [x for x in c.get(f"/api/agents/{aid}/findings").json() if x["type"] == "scope_drift"]
            f = fs[0] if fs else None
            time.sleep(0.1)
        assert f is not None, "no drift finding"
        time.sleep(1.5)  # a background explanation fill, if any, would land by now
        f = next(x for x in c.get(f"/api/agents/{aid}/findings").json() if x["type"] == "scope_drift")
        text = f["explanation"]
        assert "transfer_funds" in text and "4750000" in text and "250000" in text, text
        assert not (f.get("detail") or {}).get("explanation_model")
        assert calls == [], f"LLM/mock called for the explanation: {calls}"


def test_ac5_health_reports_llm_provider(monkeypatch, t4_env):
    for prov in ("mock", "gateway"):
        monkeypatch.setenv("ACT_LLM_PROVIDER", prov)
        for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters")]:
            del sys.modules[m]
        main = _mod("backend.main")
        with TestClient(main.app) as c:
            body = c.get("/api/health").json()
        assert body.get("llm_provider") == prov, body


def test_ac5_mock_spans_tagged_with_provider(trained_model, monkeypatch):
    posts = []

    class ActH(BaseHTTPRequestHandler):
        def do_POST(self):
            posts.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    class TokH(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"tok")

        def log_message(self, *a):
            pass
    act = HTTPServer(("127.0.0.1", 0), ActH)
    tok = HTTPServer(("127.0.0.1", 0), TokH)
    for s in (act, tok):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    monkeypatch.setenv("ACT_TRACES_URL", f"http://127.0.0.1:{act.server_port}/v1/traces")
    monkeypatch.setenv("ACT_TOKEN_URL", f"http://127.0.0.1:{tok.server_port}/access-token")
    monkeypatch.setenv("DOMINO_API_PROXY", "")
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("RECON_AGENT_ID", LOCAL_NATIVE)
    monkeypatch.delenv("BREAK_CLASSIFIER_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    agent = _mod("agents.recon_agent.agent")
    try:
        agent.run("Reconcile the GL and custodian ledgers")
    finally:
        act.shutdown()
        tok.shutdown()
    assert posts
    tagged = 0
    for body in posts:
        for rs in json.loads(body)["resourceSpans"]:
            res = {a["key"]: next(iter(a["value"].values())) for a in rs.get("resource", {}).get("attributes", [])}
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    if res.get("act.llm.provider") == "mock" or _attrs(sp).get("act.llm.provider") == "mock":
                        tagged += 1
    assert tagged > 0, "no exported span or resource carries act.llm.provider=mock"


# ---------------------------------------------------------------- 6. local agents
def _no_apps_http(monkeypatch):
    import requests

    class R:
        status_code = 200
        text = "tok"
        content = b"{}"
        headers = {"content-type": "application/json"}

        def json(self):
            return {"items": []}

        def raise_for_status(self):
            pass
    f = lambda *a, **k: R()  # noqa: E731
    monkeypatch.setattr(requests, "get", f)
    monkeypatch.setattr(requests, "post", f)
    monkeypatch.setattr(requests, "request", f)
    monkeypatch.setattr(requests.Session, "request", f)
    monkeypatch.setattr(requests.Session, "get", f)


def _fresh_intake(monkeypatch, db_env):
    monkeypatch.setenv("DOMINO_API_HOST", "http://domino.invalid")
    monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-123")
    monkeypatch.delenv("ACT_URL", raising=False)
    _no_apps_http(monkeypatch)
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters")]:
        del sys.modules[m]
    out = _mod("jobs.intake").run_intake()
    from backend import db
    with db.engine.connect() as cx:
        rows = cx.execute(db.agents.select()).fetchall()
    return out, rows


def test_ac6_local_agents_listed_with_hosting_note(db_env, monkeypatch):
    monkeypatch.setenv("ACT_DOMINO_LOCAL_AGENTS", str(ROOT / "config" / "local_agents.json"))
    monkeypatch.delenv("BREAK_CLASSIFIER_REGISTRY_VERSION", raising=False)
    out, rows = _fresh_intake(monkeypatch, db_env)
    mine = [r for r in rows if r.name == "recon-agent"]
    assert len(mine) == 1, [r.name for r in rows]
    r = mine[0]
    assert r.id == LOCAL_ACT_ID and r.source_platform == "domino" and r.native_id == LOCAL_NATIVE
    assert "workspace (not deployed)" in json.dumps(r.record)
    rec = json.dumps(r.record)
    for t in ("fetch_ledger", "compare_entries", "propose_adjustment"):
        assert t in rec
    assert "break_classifier" in rec


def test_ac6_registry_version_env_overrides_default(db_env, monkeypatch):
    monkeypatch.setenv("ACT_DOMINO_LOCAL_AGENTS", str(ROOT / "config" / "local_agents.json"))
    monkeypatch.setenv("BREAK_CLASSIFIER_REGISTRY_VERSION", "9")
    _, rows = _fresh_intake(monkeypatch, db_env)
    r = next(r for r in rows if r.name == "recon-agent")
    assert '"9"' in json.dumps(r.record) or "'9'" in str(r.record) or 9 in _walk(r.record)


def _walk(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)
    else:
        yield o


def test_ac6_not_listed_without_env(db_env, monkeypatch):
    monkeypatch.delenv("ACT_DOMINO_LOCAL_AGENTS", raising=False)
    _, rows = _fresh_intake(monkeypatch, db_env)
    assert not [r for r in rows if r.name == "recon-agent"]


# ---------------------------------------------------------------- 7. in-process validation fallback
def test_ac7_validate_main_writes_in_process(db_env, trained_model, monkeypatch):
    for m in [m for m in sys.modules if m.split(".")[0] in ("backend", "jobs", "adapters")]:
        del sys.modules[m]
    from backend import db
    db.init_db()
    with db.engine.begin() as cx:
        cx.execute(db.agents.insert().values(id=LOCAL_ACT_ID, source_platform="domino", native_id=LOCAL_NATIVE,
                                             name="recon-agent", owner="o", status="discovered", tier="2",
                                             record={"id": LOCAL_ACT_ID}))
    monkeypatch.delenv("ACT_URL", raising=False)
    monkeypatch.delenv("ACT_AGENT_ID", raising=False)
    monkeypatch.delenv("ACT_TRACES_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("BREAK_CLASSIFIER_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("RECON_AGENT_ID", LOCAL_NATIVE)
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "projx")
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "alice")
    monkeypatch.setitem(sys.modules, "mlflow", FakeMlflow())
    import requests

    def boom(*a, **k):
        raise AssertionError("HTTP used by in-process validation")
    monkeypatch.setattr(requests, "post", boom)
    v = _mod("jobs.validate")
    assert v.main() == 0
    with db.engine.connect() as cx:
        rows = cx.execute(db.validation_records.select().where(
            db.validation_records.c.agent_id == LOCAL_ACT_ID)).fetchall()
    assert len(rows) == 1
    d = rows[0].detail
    d = json.loads(d) if isinstance(d, str) else d
    assert d["judge_model_id"] == "mock-llm"


# ---------------------------------------------------------------- 8. docs
def test_ac8_integration_doc_names_every_switch():
    p = ROOT / "docs" / "INTEGRATION.md"
    assert p.is_file()
    t = p.read_text()
    for k in ("ACT_LLM_PROVIDER", "LLM_GATEWAY_URL", "ACT_DOMINO_LOCAL_AGENTS", "RECON_AGENT_ID",
              "ACT_SERVICE_ACCOUNTS", "BREAK_CLASSIFIER_ENDPOINT_URL", "ACT_URL"):
        assert k in t, k


# ---------------------------------------------------------------- round 2: local agents file robustness
@pytest.mark.parametrize("kind", ["missing", "invalid"])
def test_ac4_bad_local_agents_file_does_not_break_intake(kind, db_env, monkeypatch, tmp_path):
    if kind == "missing":
        path = tmp_path / "nope.json"
    else:
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json")
    monkeypatch.setenv("ACT_DOMINO_LOCAL_AGENTS", str(path))
    apps_calls = []

    def counting(mp):
        import requests

        class R:
            status_code = 200
            text = "tok"
            content = b"{}"
            headers = {"content-type": "application/json"}

            def json(self):
                return {"items": []}

            def raise_for_status(self):
                pass
        f = lambda *a, **k: apps_calls.append(a) or R()  # noqa: E731
        for n in ("get", "post", "request"):
            mp.setattr(requests, n, f)
        mp.setattr(requests.Session, "request", f)
        mp.setattr(requests.Session, "get", f)
    monkeypatch.setitem(globals(), "_no_apps_http", counting)
    out, rows = _fresh_intake(monkeypatch, db_env)
    names = {r.name for r in rows}
    assert "treasury-sweep-agent" in names, names
    assert "recon-agent" not in names
    assert not [e for e in out["errors"] if "domino" in e.lower()], out["errors"]
    assert apps_calls, "Domino Apps listing did not run"


# ---------------------------------------------------------------- round 2: docs and config
def test_ac5_doc_does_not_overclaim_labels_and_has_cleanup():
    t = (ROOT / "docs" / "INTEGRATION.md").read_text()
    low = t.lower()
    assert "template text" in low
    assert "explanation_model` `mock-llm`" not in t, "doc still claims drift explanations carry model mock-llm"
    assert "act:domino" in t and "local-recon-agent" in t
    assert any(w in low for w in ("archive", "remove the", "delete the")), "no go-live cleanup of old row"
    assert "repo root" in low


def test_ac6_default_owner_is_neutral_placeholder():
    raw = (ROOT / "config" / "local_agents.json").read_text()
    assert "nick_goble" not in raw and "nick" not in raw.lower()
    assert "ACT_LOCAL_AGENT_OWNER" in raw


def test_ac6_owner_env_overrides(db_env, monkeypatch):
    monkeypatch.setenv("ACT_DOMINO_LOCAL_AGENTS", str(ROOT / "config" / "local_agents.json"))
    monkeypatch.setenv("ACT_LOCAL_AGENT_OWNER", "ops-team-xyz")
    _, rows = _fresh_intake(monkeypatch, db_env)
    r = next(r for r in rows if r.name == "recon-agent")
    assert "ops-team-xyz" in json.dumps(r.record)


def test_ac2_cli_mock_run_names_real_breaks(trained_model, tmp_path):
    import os
    import subprocess
    repo = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    for k in ("ACT_TRACES_URL", "BREAK_CLASSIFIER_ENDPOINT_URL", "DOMINO_RUN_ID"):
        env.pop(k, None)
    env.update({
        "ACT_LLM_PROVIDER": "mock",
        "RECON_AGENT_ID": "local-recon-agent",
        "MLFLOW_TRACKING_URI": f"file:{tmp_path}/mlruns",
        "BREAK_CLASSIFIER_MODEL_PATH": str(trained_model),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    r = subprocess.run(
        [sys.executable, "-m", "agents.recon_agent", "Reconcile the GL and custodian ledgers"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=120)
    out = r.stdout
    assert r.returncode == 0, r.stderr[-2000:]
    assert "no breaks found" not in out.lower(), out
    assert "TXN-007" in out, out
    assert "TXN-012" in out or "TXN-014" in out, out
