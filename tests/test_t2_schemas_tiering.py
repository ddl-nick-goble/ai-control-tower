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


FINDING = {"type": "scope_drift", "severity": "high", "agent_id": "a1", "run_id": "r1",
           "span_id": "s1", "tool": "search", "ts": "2026-01-01T00:00:00Z"}


def test_ac6_finding_scope_drift_valid():
    validator("finding.json").validate(FINDING)


@pytest.mark.parametrize("k", ["tool", "span_id", "ts", "run_id"])
def test_ac6_scope_drift_requires_field(k):
    f = {x: y for x, y in FINDING.items() if x != k}
    with pytest.raises(jsonschema.ValidationError):
        validator("finding.json").validate(f)


@pytest.mark.parametrize("reason", ["tool_not_in_manifest", "value_limit_exceeded"])
def test_ac6_scope_drift_reasons(reason):
    f = {**FINDING, "reason": reason}
    if reason == "value_limit_exceeded":
        f.update(observed_value=5000, limit=1000)
    validator("finding.json").validate(f)


def test_ac6_value_limit_breach_is_scope_drift_with_observed_and_limit():
    f = {**FINDING, "tool": "transfer_funds", "reason": "value_limit_exceeded",
         "observed_value": 250000, "limit": 10000}
    validator("finding.json").validate(f)
    props = load_schema("finding.json").get("properties", {})
    for k in ("reason", "observed_value", "limit"):
        assert k in props, k


def test_ac6_rejects_value_limit_type():
    with pytest.raises(jsonschema.ValidationError):
        validator("finding.json").validate({**FINDING, "type": "value_limit"})


def test_ac6_rejects_bad_reason():
    with pytest.raises(jsonschema.ValidationError):
        validator("finding.json").validate({**FINDING, "reason": "because"})


def test_ac6_shadow_agent_valid_without_tool():
    f = {"type": "shadow_agent", "severity": "medium", "agent_id": "unknown-1", "run_id": "r1",
         "ts": "2026-01-01T00:00:00Z"}
    validator("finding.json").validate(f)


SECTION_IDS = ["identity_ownership", "tier_criteria", "permitted_actions", "validation",
               "monitoring_findings", "approvals_overrides", "records_retention"]


def _pack(ids=None):
    ids = SECTION_IDS if ids is None else ids
    cit = [{"source": "audit", "ref": "e1"}]
    return {"agent_id": "a1", "generated_at": "2026-01-01T00:00:00Z",
            "sections": [{"id": i, "title": i.replace("_", " ").title(), "content": "text",
                          "citations": copy.deepcopy(cit)} for i in ids]}


def test_ac7_evidence_pack_valid():
    validator("evidence-pack.json").validate(_pack())


def test_ac7_evidence_pack_sections_is_array_of_seven():
    sec = load_schema("evidence-pack.json")["properties"]["sections"]
    assert sec["type"] == "array"
    assert sec.get("minItems") == 7 and sec.get("maxItems") == 7


def test_ac7_evidence_pack_rejects_swapped_order():
    ids = list(SECTION_IDS)
    ids[0], ids[1] = ids[1], ids[0]
    with pytest.raises(jsonschema.ValidationError):
        validator("evidence-pack.json").validate(_pack(ids))


def test_ac7_evidence_pack_rejects_empty_citations():
    p = _pack()
    p["sections"][3]["citations"] = []
    with pytest.raises(jsonschema.ValidationError):
        validator("evidence-pack.json").validate(p)


def test_ac7_evidence_pack_rejects_six_sections():
    with pytest.raises(jsonschema.ValidationError):
        validator("evidence-pack.json").validate(_pack(SECTION_IDS[:6]))


@pytest.mark.parametrize("k", ["id", "title", "content", "citations"])
def test_ac7_evidence_pack_section_requires_field(k):
    p = _pack()
    del p["sections"][2][k]
    with pytest.raises(jsonschema.ValidationError):
        validator("evidence-pack.json").validate(p)


EXTERNAL = {
    **RECORD,
    "id": "ext-1",
    "origin": "external",
    "status": "pending_review",
    "card_signature": "signed",
    "passport": {"identity": "https://partner.example.com/agent",
                 "allowed_skills": ["triage"], "data_scopes": ["disputes"],
                 "expires_at": "2027-01-01T00:00:00Z"},
}


def test_ac11_external_agent_record_valid():
    validator("agent-record.json").validate(EXTERNAL)


@pytest.mark.parametrize("sig", ["signed", "unsigned", "invalid"])
def test_ac11_card_signature_values(sig):
    validator("agent-record.json").validate({**EXTERNAL, "card_signature": sig})


def test_ac11_card_signature_bogus_rejected():
    with pytest.raises(jsonschema.ValidationError):
        validator("agent-record.json").validate({**EXTERNAL, "card_signature": "bogus"})


def test_ac11_origin_defined():
    props = load_schema("agent-record.json")["properties"]
    for k in ("origin", "card_signature", "passport"):
        assert k in props, k


def test_ac1_no_distinct_identity_validates():
    r = {**RECORD, "identity": {"type": "none", "distinct": False}}
    validator("agent-record.json").validate(r)


def test_ac9_registered_by_accepted():
    validator("agent-record.json").validate({**RECORD, "registered_by": "alice"})
    assert "registered_by" in load_schema("agent-record.json")["properties"]


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
        assert "no_benchmark_rule" in str(e.value)


def test_ac3_modified_config_weights_change_tier():
    from backend.tiering import score_tier
    cfg = yaml.safe_load((ROOT / "config" / "tiering.yaml").read_text())
    crit = _crit(1)
    first = next(iter(crit))
    crit[first] = 5
    base = score_tier(crit, config=cfg)
    mod = copy.deepcopy(cfg)
    for n, v in mod["criteria"].items():
        v["weight"] = 1.0 if n == first else 0.0
    changed = score_tier(crit, config=mod)
    assert base.tier == 4
    assert changed.tier == 1
    assert changed.tier != base.tier


def _scores(v):
    return {n: v for n in CRITERIA}


def test_ac3_all_fours_is_tier1_boundary():
    # weighted mean is exactly 4.0; guards float rounding at the threshold
    from backend.tiering import score_tier
    assert score_tier(_scores(4)).tier == 1


def test_ac3_all_threes_is_tier2():
    from backend.tiering import score_tier
    assert score_tier(_scores(3)).tier == 2


def test_ac3_all_twos_is_tier3():
    from backend.tiering import score_tier
    assert score_tier(_scores(2)).tier == 3


def test_ac3_non_uniform_weights():
    # heavy criteria (weights 0.2/0.2/0.15/0.15) at 5, light ones at 2:
    # weighted mean 4.1 -> Tier 1; unweighted mean 3.71 would be Tier 2.
    from backend.tiering import score_tier
    cfg = yaml.safe_load((ROOT / "config" / "tiering.yaml").read_text())
    for n, w in {"decision_impact": 0.20, "financial_exposure": 0.20, "customer_impact": 0.15,
                 "autonomy": 0.15, "data_sensitivity": 0.10, "reversibility": 0.10,
                 "regulatory_exposure": 0.10}.items():
        cfg["criteria"][n]["weight"] = w
    s = _scores(2)
    for n in ("decision_impact", "financial_exposure", "customer_impact", "autonomy"):
        s[n] = 5
    assert score_tier(s, config=cfg).tier == 1
