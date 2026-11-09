"""T-7 tests: recon agent, break_classifier, OTLP span export to ACT.

Assumed contracts (builder must match):
- agents.recon_agent.tools: fetch_ledger(name), compare_entries(ledger_a, ledger_b),
  propose_adjustment(...) ; two fixture files under agents/recon_agent/data/
- models.break_classifier.train.train(output_dir) writes a model file into output_dir;
  models/break_classifier/predict.py predict(payload: dict) -> {"class": str},
  loading the model from env BREAK_CLASSIFIER_MODEL_PATH
- agents.recon_agent.agent.run(prompt: str) -> dict
- agents.recon_agent.llm.create_message(...) is the only gateway entry point; the stub
  returns an Anthropic-style dict {"stop_reason", "content": [blocks]} with
  {"type": "tool_use", "id", "name", "input"} / {"type": "text", "text"} blocks.
- Tracing hook posts OTLP/HTTP JSON to env ACT_TRACES_URL with Bearer token from
  http://localhost:8899/access-token (stubbed via requests/urllib is NOT patched; the test
  sets DOMINO_TOKEN_URL-independent behavior by patching a callable if present; see
  fixture `token_stub`, which serves the token over a local HTTP server and sets
  ACT_TOKEN_URL, falling back on the default URL being unreachable).
"""
import importlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "stub-token-xyz"


def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _attrs(span):
    out = {}
    for a in span.get("attributes", []):
        v = a["value"]
        out[a["key"]] = next(iter(v.values())) if v else None
    return out


def test_t7_tools_exist():
    tools = importlib.import_module("agents.recon_agent.tools")
    for n in ("fetch_ledger", "compare_entries", "propose_adjustment"):
        assert callable(getattr(tools, n)), n
    files = sorted(p for p in (ROOT / "agents/recon_agent/data").glob("*") if p.is_file())
    assert len(files) >= 2
    a = tools.fetch_ledger(files[0].stem)
    b = tools.fetch_ledger(files[1].stem)
    assert a and b
    breaks = tools.compare_entries(a, b)
    assert len(breaks) >= 1


@pytest.fixture
def trained_model(tmp_path, monkeypatch):
    train = importlib.import_module("models.break_classifier.train")
    train.train(str(tmp_path))
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files, "train() wrote no model file"
    path = next((p for p in files if p.suffix in (".pkl", ".joblib")), files[0])
    monkeypatch.setenv("BREAK_CLASSIFIER_MODEL_PATH", str(path))
    return path


def test_t7_break_classifier_predict_returns_class(trained_model):
    predict = importlib.import_module("models.break_classifier.predict")
    out = predict.predict({"amount_diff": 12.5, "days_diff": 2, "missing_side": 0})
    assert isinstance(out, dict)
    assert isinstance(out["class"], str) and out["class"]


AGENT_ID = "dom-agent-test-123"


def _run_agent(monkeypatch, classifier_url=None, access_key=None, stub_posts=None):
    """Run the agent with a scripted LLM against local ACT/token stubs.
    Returns (result, posts, servers)."""
    posts = []

    class ActH(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            posts.append((self.path, dict(self.headers), self.rfile.read(n)))
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
            self.wfile.write(TOKEN.encode())

        def log_message(self, *a):
            pass

    act, tok = _serve(ActH), _serve(TokH)
    monkeypatch.setenv("ACT_TRACES_URL", f"http://127.0.0.1:{act.server_port}/v1/traces")
    monkeypatch.setenv("ACT_TOKEN_URL", f"http://127.0.0.1:{tok.server_port}/access-token")
    monkeypatch.setenv("DOMINO_API_PROXY", "")
    monkeypatch.setenv("RECON_AGENT_ID", AGENT_ID)
    if classifier_url:
        monkeypatch.setenv("BREAK_CLASSIFIER_ENDPOINT_URL", classifier_url)
    else:
        monkeypatch.delenv("BREAK_CLASSIFIER_ENDPOINT_URL", raising=False)
    if access_key:
        monkeypatch.setenv("BREAK_CLASSIFIER_ACCESS_KEY", access_key)
    else:
        monkeypatch.delenv("BREAK_CLASSIFIER_ACCESS_KEY", raising=False)

    names = sorted(p.stem for p in (ROOT / "agents/recon_agent/data").glob("*") if p.is_file())
    script = [
        ("fetch_ledger", {"name": names[0]}),
        ("fetch_ledger", {"name": names[1]}),
        ("compare_entries", {"ledger_a": names[0], "ledger_b": names[1]}),
        ("propose_adjustment", {"break_id": "1", "reason": "timing"}),
    ]
    state = {"i": 0}

    def fake_create_message(*args, **kwargs):
        i = state["i"]
        state["i"] += 1
        if i < len(script):
            name, inp = script[i]
            return {"stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": f"tu{i}", "name": name, "input": inp}]}
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]}

    llm = importlib.import_module("agents.recon_agent.llm")
    monkeypatch.setattr(llm, "create_message", fake_create_message)
    agent = importlib.import_module("agents.recon_agent.agent")
    if hasattr(agent, "create_message"):
        monkeypatch.setattr(agent, "create_message", fake_create_message)

    result = agent.run("Reconcile the two ledgers")
    act.shutdown()
    tok.shutdown()
    return result, posts


def _collect_spans(posts):
    """Return list of (resource_attrs, span, span_attrs)."""
    out = []
    for _, _, body in posts:
        doc = json.loads(body)
        assert "resourceSpans" in doc
        for rs in doc["resourceSpans"]:
            rattrs = _attrs(rs.get("resource", {}))
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    out.append((rattrs, sp, _attrs(sp)))
    return out


def test_t7_agent_run_posts_otel_spans(trained_model, monkeypatch):
    result, posts = _run_agent(monkeypatch)
    assert isinstance(result, dict)
    assert posts, "no POST reached ACT"
    assert any(h.get("Authorization") == f"Bearer {TOKEN}" for _, h, _ in posts)
    spans = _collect_spans(posts)
    attrs = [a for _, _, a in spans]
    invoke = [a for a in attrs if a.get("gen_ai.operation.name") == "invoke_agent"]
    assert invoke and any(a.get("gen_ai.agent.id") for a in invoke)
    tools = {a.get("gen_ai.tool.name") for a in attrs if a.get("gen_ai.operation.name") == "execute_tool"}
    assert {"fetch_ledger", "compare_entries", "propose_adjustment"} <= tools


def test_t7_agent_id_on_every_span(trained_model, monkeypatch):
    _, posts = _run_agent(monkeypatch)
    spans = _collect_spans(posts)
    invoke = [a for _, _, a in spans if a.get("gen_ai.operation.name") == "invoke_agent"]
    assert invoke and all(a.get("gen_ai.agent.id") == AGENT_ID for a in invoke)
    checked = 0
    for r, _, a in spans:
        if a.get("gen_ai.operation.name") in ("execute_tool", "chat"):
            checked += 1
            resolved = a.get("gen_ai.agent.id") or r.get("gen_ai.agent.id")
            assert resolved == AGENT_ID, a
    assert checked >= 3


def test_t7_recon_agent_id_required(monkeypatch):
    monkeypatch.delenv("RECON_AGENT_ID", raising=False)
    llm = importlib.import_module("agents.recon_agent.llm")
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "x"}]}

    monkeypatch.setattr(llm, "create_message", fake)
    agent = importlib.import_module("agents.recon_agent.agent")
    if hasattr(agent, "create_message"):
        monkeypatch.setattr(agent, "create_message", fake)
    with pytest.raises((RuntimeError, ValueError), match="RECON_AGENT_ID"):
        agent.run("Reconcile the two ledgers")
    assert not calls, "LLM called before RECON_AGENT_ID check"


def _classifier_spans(posts):
    return [a for _, _, a in _collect_spans(posts) if "act.classifier.source" in a]


def test_t7_classifier_source_local(trained_model, monkeypatch):
    _, posts = _run_agent(monkeypatch)
    cs = _classifier_spans(posts)
    assert cs, "no span carries act.classifier.source"
    assert all(a["act.classifier.source"] == "local" for a in cs)


def test_t7_agent_endpoint_uses_basic_auth(trained_model, monkeypatch):
    import base64
    got = []

    class ClsH(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            got.append((dict(self.headers), self.rfile.read(n)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"result": {"class": "timing"}}).encode())

        def log_message(self, *a):
            pass

    cls = _serve(ClsH)
    try:
        _, posts = _run_agent(monkeypatch,
                              classifier_url=f"http://127.0.0.1:{cls.server_port}/score",
                              access_key="k1")
    finally:
        cls.shutdown()
    assert got, "classifier endpoint stub was never called"
    for headers, body in got:
        auth = headers.get("Authorization", "")
        assert auth.startswith("Basic "), auth
        assert base64.b64decode(auth[6:]).decode() == "k1:k1"
        assert "bearer" not in auth.lower()
        assert "data" in json.loads(body)
    cs = _classifier_spans(posts)
    assert cs and all(a["act.classifier.source"] == "endpoint" for a in cs)


def test_t7_experiment_naming_uses_root_modules(monkeypatch):
    assert not (ROOT / "agents/recon_agent/experiment_setup.py").exists()
    assert not (ROOT / "agents/recon_agent/tracing_setup.py").exists()
    for f in ("agents/recon_agent/agent.py", "models/break_classifier/train.py"):
        src = (ROOT / f).read_text()
        assert "agents.recon_agent.experiment_setup" not in src, f
        assert "agents.recon_agent.tracing_setup" not in src, f
    import mlflow
    es = importlib.import_module("experiment_setup")
    importlib.import_module("tracing_setup")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "projx")
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "usery")
    monkeypatch.setattr(mlflow, "set_experiment", lambda name, *a, **k: None)
    assert es.setup_experiment() == "act-recon-projx-usery"


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("BREAK_CLASSIFIER_ENDPOINT_URL"), reason="BREAK_CLASSIFIER_ENDPOINT_URL not set")
@pytest.mark.skipif(not os.environ.get("BREAK_CLASSIFIER_ACCESS_KEY"), reason="BREAK_CLASSIFIER_ACCESS_KEY not set")
def test_t7_live_endpoint_returns_class():
    import requests
    key = os.environ["BREAK_CLASSIFIER_ACCESS_KEY"]
    r = requests.post(os.environ["BREAK_CLASSIFIER_ENDPOINT_URL"],
                      json={"data": {"amount_diff": 12.5, "days_diff": 2, "missing_side": 0}},
                      auth=(key, key), timeout=30)
    assert r.status_code == 200
    res = r.json()["result"]
    cls = res.get("class") if isinstance(res, dict) else res
    assert isinstance(cls, str) and cls


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ACT_LIVE_URL"), reason="ACT_LIVE_URL not set")
def test_t7_live_agent_spans_at_act():
    """Placeholder: run the deployed agent once, then assert ACT shows events for it.
    Verified manually/by ui-tester until a stable ACT events query route exists."""
    pytest.skip("placeholder: query ACT events for the agent after a live run")
