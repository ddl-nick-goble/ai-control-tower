"""T-3 fixture tests (AC-1 data, AC-12 data). Written from the spec, not the implementation."""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"


# ---------- helpers ----------
def jfiles(sub):
    d = FIX / sub
    return sorted(d.rglob("*.json")) if d.exists() else []


def load(p):
    return json.loads(Path(p).read_text())


def walk(o):
    yield o
    if isinstance(o, dict):
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def list_items(sub, *keys):
    """Collect items from top-level list(s) under the given keys across files in a dir."""
    out = []
    for f in jfiles(sub):
        d = load(f)
        for k in keys:
            if isinstance(d, dict) and isinstance(d.get(k), list):
                out += d[k]
    return out


def require(sub):
    files = jfiles(sub)
    assert files, f"fixtures/{sub}/ has no JSON fixture files (fixtures missing)"
    return files


def attrs(span):
    res = {}
    for a in span.get("attributes", []):
        v = a.get("value", {})
        res[a["key"]] = next(iter(v.values())) if isinstance(v, dict) and v else v
    return res


def spans_of(doc):
    out = []
    for rs in doc.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            out += ss.get("spans", [])
    return out


def all_run_files():
    return jfiles("runs")


# ---------- AC-1: feeds ----------
def test_ac1_all_fixture_files_valid_json():
    files = [p for p in FIX.rglob("*.json")] if FIX.exists() else []
    assert files, "no fixture JSON files exist (fixtures missing)"
    for p in files:
        json.loads(p.read_text())


def test_ac1_fixture_counts_per_platform():
    require("aws"); require("google"); require("microsoft"); require("databricks")
    aws = list_items("aws", "agentRuntimes")
    assert len(aws) == 3, f"AWS AgentCore count {len(aws)}"
    for a in aws:
        for k in ("agentRuntimeId", "agentRuntimeArn", "agentRuntimeName"):
            assert a.get(k), f"AWS runtime missing {k}"
    ms = list_items("microsoft", "value")
    assert len(ms) == 3, f"Agent 365 count {len(ms)}"
    db = [x for f in jfiles("databricks") for x in walk(load(f))
          if isinstance(x, list) and x and all(isinstance(i, dict) for i in x)]
    # Databricks: the largest list of dicts at top level is the agent list
    top = []
    for f in jfiles("databricks"):
        d = load(f)
        if isinstance(d, dict):
            for v in d.values():
                if isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
                    top += v
    assert len(top) == 2, f"Databricks count {len(top)}"
    goog = []
    for f in jfiles("google"):
        d = load(f)
        if isinstance(d, dict):
            for v in d.values():
                if isinstance(v, list):
                    goog += [i for i in v if isinstance(i, dict) and "name" in i]
    assert len(goog) == 3, f"Google count {len(goog)}"
    for g in goog:
        assert g["name"].startswith("projects/") and "/locations/" in g["name"]
    assert len(aws) + len(ms) + len(top) + len(goog) == 11


def test_ac1_aws_registry_response_present():
    require("aws")
    text = " ".join(f.read_text() for f in jfiles("aws"))
    assert "agentRuntimes" in text
    # ListDiscoverableRegistryRecords response: some file other than the runtime list or a records key
    assert any(
        isinstance(load(f), dict) and any("record" in k.lower() for k in load(f))
        for f in jfiles("aws")
    ), "no Agent Registry ListDiscoverableRegistryRecords response (a top-level key containing 'record')"


def test_ac1_microsoft_odata_shape():
    files = require("microsoft")
    d = load(files[0])
    assert isinstance(d.get("value"), list)
    assert any(k.startswith("@odata") for k in d), "expected OData annotations (@odata.*)"


def test_ac1_databricks_unity_catalog_shape():
    files = require("databricks")
    text = " ".join(f.read_text() for f in files)
    assert "agent" in text.lower()
    assert any(isinstance(load(f), dict) for f in files)


def test_ac1_includes_treasury_sweep_agent_in_aws():
    require("aws")
    names = [a.get("agentRuntimeName") for a in list_items("aws", "agentRuntimes")]
    assert "treasury-sweep-agent" in names


def test_ac1_owner_tools_identity_discoverable_each_platform():
    for sub in ("aws", "google", "microsoft", "databricks"):
        files = require(sub)
        text = " ".join(f.read_text() for f in files).lower()
        assert "owner" in text, f"{sub}: no owner info"
        assert "tool" in text, f"{sub}: no tools info"
        assert any(w in text for w in ("identity", "principal", "serviceaccount", "service_account",
                                       "managedidentity", "clientid")), f"{sub}: no identity info"


def test_ac1_one_agent_per_platform_lacks_distinct_identity():
    """Four total (one per platform): look for agents whose identity is explicitly shared/none."""
    for sub in ("aws", "google", "microsoft", "databricks"):
        require(sub)
        flagged = 0
        for f in jfiles(sub):
            for o in walk(load(f)):
                if isinstance(o, dict):
                    if o.get("distinct") is False or o.get("identityDistinct") is False \
                            or o.get("distinct_identity") is False:
                        flagged += 1
                    elif isinstance(o.get("identity"), dict) and (
                            o["identity"].get("type") == "none" or o["identity"].get("distinct") is False):
                        flagged += 1
        assert flagged == 1, f"{sub}: expected exactly 1 agent without distinct identity, found {flagged}"


# ---------- AC-1: runs / dialects ----------
def _dialect_files():
    otel, oi, ml = [], [], []
    for f in all_run_files():
        t = f.read_text()
        if "gen_ai.agent.id" in t or "invoke_agent" in t:
            otel.append(f)
        if "openinference.span.kind" in t:
            oi.append(f)
        if "mlflow.spanType" in t:
            ml.append(f)
    return otel, oi, ml


def test_ac1_run_files_are_otlp_json():
    files = require("runs")
    for f in files:
        d = load(f)
        assert "resourceSpans" in d, f"{f.name}: not OTLP/HTTP JSON"
        assert spans_of(d), f"{f.name}: no spans"


def test_ac1_three_span_dialects_present():
    require("runs")
    otel, oi, ml = _dialect_files()
    assert otel, "no OTel GenAI file"
    assert oi, "no OpenInference file"
    assert ml, "no MLflow file"


def test_ac1_otel_genai_attributes():
    otel, _, _ = _dialect_files()
    assert otel, "no OTel GenAI file"
    keys, names = set(), set()
    for f in otel:
        for s in spans_of(load(f)):
            keys |= set(attrs(s)); names.add(s.get("name", "").split(" ")[0])
    assert {"gen_ai.agent.id", "gen_ai.operation.name", "gen_ai.tool.name"} <= keys
    assert {"invoke_agent", "execute_tool"} <= names


def test_ac1_openinference_attributes():
    _, oi, _ = _dialect_files()
    assert oi, "no OpenInference file"
    kinds, keys = set(), set()
    for f in oi:
        for s in spans_of(load(f)):
            a = attrs(s); keys |= set(a)
            if "openinference.span.kind" in a:
                kinds.add(a["openinference.span.kind"])
    assert {"AGENT", "TOOL"} <= kinds
    assert "tool.name" in keys


def test_ac1_mlflow_attributes():
    _, _, ml = _dialect_files()
    assert ml, "no MLflow file"
    keys = set()
    for f in ml:
        for s in spans_of(load(f)):
            keys |= set(attrs(s))
    assert "mlflow.spanType" in keys


# ---------- external agent card ----------
def test_ac1_agent_card_fields():
    """A2A 1.0 Agent Card schema."""
    p = FIX / "external" / "agent-card.json"
    assert p.exists(), "fixtures/external/agent-card.json missing"
    c = load(p)
    for k in ("name", "description", "version", "skills", "capabilities",
              "defaultInputModes", "defaultOutputModes", "supportedInterfaces"):
        assert k in c, f"agent card missing {k}"
    assert isinstance(c["skills"], list) and c["skills"]
    for sk in c["skills"]:
        for k in ("id", "name", "description", "tags"):
            assert sk.get(k), f"skill missing {k}"
        assert isinstance(sk["tags"], list)
    assert isinstance(c["capabilities"], dict)
    assert isinstance(c["defaultInputModes"], list) and isinstance(c["defaultOutputModes"], list)
    ifs = c["supportedInterfaces"]
    assert isinstance(ifs, list) and ifs, "supportedInterfaces must be a non-empty list"
    for i in ifs:
        for k in ("url", "protocolBinding", "protocolVersion"):
            assert i.get(k), f"supportedInterfaces item missing {k}"
        assert str(i["protocolVersion"]).startswith("1."), i["protocolVersion"]


def test_ac1_agent_card_has_no_pre_1_0_top_level_keys():
    c = load(FIX / "external" / "agent-card.json")
    for k in ("url", "preferredTransport", "protocolVersion"):
        assert k not in c, f"pre-1.0 top-level key {k} must not be present"


def test_ac1_agent_card_served_over_http():
    serve = FIX / "external" / "serve.py"
    assert serve.exists(), "fixtures/external/serve.py missing"
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen([sys.executable, str(serve), "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        body = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=2) as r:
                    assert r.status == 200
                    body = json.loads(r.read())
                break
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
        assert body is not None, "server did not serve the card"
        assert body == load(FIX / "external" / "agent-card.json")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ---------- AC-12: weekend drift ----------
DRIFT = FIX / "runs" / "weekend-drift.json"


def _manifest():
    for f in jfiles("aws"):
        for o in walk(load(f)):
            if isinstance(o, dict) and "treasury-sweep-agent" in [
                    v for v in o.values() if isinstance(v, str)]:
                tools, limits = None, None
                for p in walk(o):
                    if isinstance(p, dict):
                        tools = p.get("tools", tools)
                        limits = p.get("value_limits", limits)
                if tools is not None:
                    return tools, limits
    return None, None


def _tool_names(tools):
    return [t if isinstance(t, str) else t.get("name") for t in tools]


def test_ac12_weekend_drift_has_40_spans():
    assert DRIFT.exists(), "fixtures/runs/weekend-drift.json missing"
    assert len(spans_of(load(DRIFT))) == 40


def test_ac12_manifest_has_transfer_funds_limit_not_list_credentials():
    tools, limits = _manifest()
    assert tools is not None, "treasury-sweep-agent manifest tools not found in fixtures/aws"
    names = _tool_names(tools)
    assert "transfer_funds" in names
    assert "list_credentials" not in names
    lim = (limits or {}).get("transfer_funds")
    if isinstance(lim, dict):
        lim = lim.get("amount")
    assert isinstance(lim, (int, float)) and not isinstance(lim, bool), f"no numeric limit: {limits}"


def test_ac12_weekend_drift_calls_and_amount_over_limit():
    assert DRIFT.exists(), "fixtures/runs/weekend-drift.json missing"
    spans = spans_of(load(DRIFT))
    def tool(s):
        a = attrs(s)
        return a.get("gen_ai.tool.name") or a.get("tool.name")
    tf = [s for s in spans if tool(s) == "transfer_funds"]
    lc = [s for s in spans if tool(s) == "list_credentials"]
    assert tf, "no transfer_funds call"
    assert lc, "no list_credentials call"
    # at 2026-11-07T02:14:00Z
    from datetime import datetime, timezone
    target = datetime(2026, 11, 7, 2, 14, tzinfo=timezone.utc).timestamp()
    def start(s):
        return int(s["startTimeUnixNano"]) / 1e9
    hit = [s for s in tf if abs(start(s) - target) < 1]
    assert hit, "no transfer_funds call at 2026-11-07T02:14:00Z"
    tools, limits = _manifest()
    assert limits and "transfer_funds" in limits, "manifest limit not found"
    lim = limits["transfer_funds"]
    lim = lim.get("amount") if isinstance(lim, dict) else lim
    execs = [s for s in hit if attrs(s).get("gen_ai.operation.name") == "execute_tool"
             or s.get("name", "").startswith("execute_tool")]
    assert execs, "transfer_funds call is not an execute_tool span"
    raw = attrs(execs[0]).get("gen_ai.tool.call.arguments")
    assert isinstance(raw, str), "execute_tool span lacks gen_ai.tool.call.arguments JSON string"
    args = json.loads(raw)
    assert isinstance(args, dict)
    amt = args.get("amount")
    assert isinstance(amt, (int, float)) and not isinstance(amt, bool), f"amount not numeric: {amt!r}"
    assert amt > lim


def test_ac12_weekend_drift_is_for_treasury_sweep_agent():
    assert DRIFT.exists(), "fixtures/runs/weekend-drift.json missing"
    assert "treasury-sweep-agent" in DRIFT.read_text()


# ---------- native ids / distinct runs / uuid shape ----------
def _native_ids():
    ids = set()
    for a in list_items("aws", "agentRuntimes"):
        ids.add(a.get("agentRuntimeId"))
    for f in jfiles("google"):
        d = load(f)
        for v in d.values() if isinstance(d, dict) else []:
            for i in v if isinstance(v, list) else []:
                if isinstance(i, dict) and str(i.get("name", "")).startswith("projects/"):
                    ids.add(i["name"]); ids.add(i["name"].rsplit("/", 1)[-1])
        for o in walk(d):
            if isinstance(o, dict) and o.get("agentEngine"):
                ids.add(o["agentEngine"]); ids.add(o["agentEngine"].rsplit("/", 1)[-1])
    for p in list_items("microsoft", "value"):
        ids.add(p.get("id"))
    for f in jfiles("databricks"):
        d = load(f)
        for v in d.values() if isinstance(d, dict) else []:
            for i in v if isinstance(v, list) else []:
                if isinstance(i, dict):
                    ids.add(i.get("agent_service_id"))
    ids.discard(None)
    assert len(ids) >= 8, f"native id set suspiciously small: {ids}"
    return ids


def _is_agent_span(s):
    a = attrs(s)
    return (a.get("gen_ai.operation.name") == "invoke_agent"
            or s.get("name", "").startswith("invoke_agent")
            or a.get("openinference.span.kind") == "AGENT"
            or a.get("mlflow.spanType") == "AGENT")


def test_ac1_every_run_agent_span_carries_native_agent_id():
    ids = _native_ids()
    files = require("runs")
    for f in files:
        d = load(f)
        res = {}
        for rs in d.get("resourceSpans", []):
            res.update(attrs(rs.get("resource", {})))
        agent_spans = [s for s in spans_of(d) if _is_agent_span(s)]
        assert agent_spans, f"{f.name}: no agent-level span"
        for s in agent_spans:
            v = attrs(s).get("gen_ai.agent.id") or res.get("gen_ai.agent.id")
            assert v, f"{f.name}: agent span lacks gen_ai.agent.id"
            assert v in ids, f"{f.name}: gen_ai.agent.id {v!r} is not a native platform id"


def test_ac1_trace_ids_distinct_between_run_files():
    files = require("runs")
    seen = {}
    for f in files:
        tids = {s.get("traceId") for s in spans_of(load(f))}
        assert len(tids) == 1 and None not in tids, f"{f.name}: expected one traceId, got {tids}"
        t = tids.pop()
        assert t not in seen, f"traceId {t} shared by {seen[t]} and {f.name}"
        seen[t] = f.name


def test_ac1_microsoft_principal_id_is_uuid():
    pk = list_items("microsoft", "value")
    assert pk
    vals = [p.get("identity", {}).get("principalId") for p in pk]
    vals = [v for v in vals if v is not None]
    assert vals, "no principalId values"
    for v in vals:
        assert str(uuid.UUID(v)) == v.lower(), f"principalId not canonical 8-4-4-4-12 UUID: {v}"


def test_ac1_databricks_application_id_is_uuid():
    n = 0
    for f in jfiles("databricks"):
        for o in walk(load(f)):
            if isinstance(o, dict) and "application_id" in o:
                v = o["application_id"]
                assert str(uuid.UUID(v)) == v.lower(), f"application_id not canonical UUID: {v}"
                n += 1
    assert n >= 1
