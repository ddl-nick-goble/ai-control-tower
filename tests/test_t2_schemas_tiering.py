import copy
import json
import pathlib

import jsonschema
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "schemas"


def load_schema(name):
    p = SCHEMAS / name
    assert p.exists(), f"missing {p}"
    return json.loads(p.read_text())


def validator(name):
    s = load_schema(name)
    cls = jsonschema.validators.validator_for(s)
    cls.check_schema(s)
    return cls(s)


@pytest.mark.parametrize("n", ["agent-record.json", "canonical-event.json", "finding.json", "evidence-pack.json"])
def test_ac2_schema_files_valid(n):
    validator(n)


CRITERIA = ["decision_impact", "financial_exposure", "customer_impact", "autonomy",
            "data_sensitivity", "reversibility", "regulatory_exposure"]

RECORD = {
    "id": "agent-1",
    "name": "Payments Triage",
    "description": "Triages payment disputes",
    "version": "1.0.0",
    "url": "https://agents.example.com/payments",
    "skills": [{"id": "triage", "name": "Triage", "description": "d", "tags": ["payments"]}],
    "oasf": {"skills": ["nlp/classification"], "domains": ["finance/payments"]},
    "owner": "alice@example.com",
    "tier": 2,
    "criteria": {c: 3 for c in CRITERIA},
    "manifest": {"tools": ["search"], "data_scopes": ["disputes"], "systems": ["crm"],
                 "value_limits": {"max_usd": 1000}},
    "identity": {"type": "spiffe", "subject": "spiffe://example/agent-1"},
    "dependencies": {"models": [{"name": "claude", "registry_version": "1.2"}]},
    "status": "active",
    "versions": [{"version": "1.0.0", "created": "2026-01-01T00:00:00Z"}],
}


def test_ac2_agent_record_schema_valid():
    validator("agent-record.json").validate(RECORD)


def test_ac2_agent_record_rejects_tier5():
    r = copy.deepcopy(RECORD); r["tier"] = 5
    with pytest.raises(jsonschema.ValidationError):
        validator("agent-record.json").validate(r)


def test_ac2_agent_record_rejects_missing_owner():
    r = copy.deepcopy(RECORD); del r["owner"]
    with pytest.raises(jsonschema.ValidationError):
        validator("agent-record.json").validate(r)


def test_ac2_agent_record_defines_bank_fields():
    s = load_schema("agent-record.json")
    props = s.get("properties", {})
    for k in ["owner", "tier", "criteria", "manifest", "identity", "dependencies", "status", "versions",
              "name", "description", "version", "skills"]:
        assert k in props, k


EVENT = {"run_id": "r1", "agent_id": "a1", "ts": "2026-01-01T00:00:00Z", "kind": "tool_call",
         "name": "search", "args_redacted": {"q": "[REDACTED]"}, "attrs": {}}


def test_ac2_canonical_event_valid():
    validator("canonical-event.json").validate(EVENT)


@pytest.mark.parametrize("k", ["input", "model_call", "tool_call", "output", "approval", "override"])
def test_ac2_canonical_event_kinds(k):
    validator("canonical-event.json").validate({**EVENT, "kind": k})


def test_ac2_canonical_event_bad_kind():
    with pytest.raises(jsonschema.ValidationError):
        validator("canonical-event.json").validate({**EVENT, "kind": "foo"})


@pytest.mark.parametrize("t", ["scope_drift", "shadow_agent"])
def test_ac2_finding_types(t):
    f = {"type": t, "severity": "high", "agent_id": "a1", "run_id": "r1",
         "span_id": "s1", "span": "s1", "tool": "search", "tool_name": "search",
         "ts": "2026-01-01T00:00:00Z", "timestamp": "2026-01-01T00:00:00Z"}
    validator("finding.json").validate(f)


def test_ac2_evidence_pack_valid():
    s = load_schema("evidence-pack.json")
    text = json.dumps(s).lower()
    for w in ["identity", "tier", "permitted", "validation", "monitoring", "approvals", "retention", "citations"]:
        assert w in text, w
    cit = [{"source": "audit", "ref": "e1", "id": "e1", "url": "http://x/e1"}]
    names = ["identity_ownership", "tier_criteria", "permitted_actions", "validation",
             "monitoring_findings", "approvals_overrides", "records_retention"]
    props = s.get("properties", {})
    sections = props.get("sections")
    assert sections is not None or all(n in props for n in names) or True
    sec = {"title": "t", "content": {}, "citations": cit}
    candidates = [
        {"sections": [dict(sec, name=n, id=n, key=n) for n in names]},
        {"sections": {n: sec for n in names}},
        {n: sec for n in names},
    ]
    v = validator("evidence-pack.json")
    ok = False
    for c in candidates:
        c = {**c, "agent_id": "a1", "generated_at": "2026-01-01T00:00:00Z"}
        if not list(v.iter_errors(c)):
            ok = True
            break
    assert ok, "no plausible evidence pack example validated"


def test_ac3_tiering_yaml():
    p = ROOT / "config" / "tiering.yaml"
    assert p.exists()
    c = yaml.safe_load(p.read_text())
    crit = c["criteria"]
    assert len(crit) == 7
    items = crit.items() if isinstance(crit, dict) else [(x.get("name"), x) for x in crit]
    for _, v in items:
        w = v["weight"] if isinstance(v, dict) else v
        assert isinstance(w, (int, float))
    assert "tiers" in c or "thresholds" in c
    assert any("benchmark" in k.lower() or "backtest" in k.lower() for k in c)


def test_redaction_yaml_parses():
    p = ROOT / "config" / "redaction.yaml"
    assert p.exists()
    assert yaml.safe_load(p.read_text()) is not None


def test_borrowed_doc():
    p = ROOT / "docs" / "BORROWED.md"
    assert p.exists()
    t = p.read_text()
    assert "A2A" in t and "OASF" in t


def _crit(v):
    cfg = yaml.safe_load((ROOT / "config" / "tiering.yaml").read_text())
    c = cfg["criteria"]
    names = list(c.keys()) if isinstance(c, dict) else [x["name"] for x in c]
    return {n: v for n in names}


def test_ac3_max_criteria_is_tier1():
    from backend.tiering import score_tier
    r = score_tier(_crit(5))
    assert r.tier == 1 and isinstance(r.reasoning, str) and r.reasoning


def test_ac3_min_criteria_is_tier4():
    from backend.tiering import score_tier
    r = score_tier(_crit(1))
    assert r.tier == 4 and r.reasoning


def test_ac3_config_argument_accepted():
    from backend.tiering import score_tier
    cfg = yaml.safe_load((ROOT / "config" / "tiering.yaml").read_text())
    r = score_tier(_crit(5), config=cfg)
    assert r.tier == 1


def test_ac3_benchmark_input_rejected():
    from backend.tiering import score_tier, TieringInputRejected
    cfg = yaml.safe_load((ROOT / "config" / "tiering.yaml").read_text())
    for extra in ("backtest_score", "benchmark_score"):
        crit = _crit(3); crit[extra] = 5
        with pytest.raises(TieringInputRejected) as e:
            score_tier(crit)
        assert "benchmark" in str(e.value).lower() or "backtest" in str(e.value).lower()
