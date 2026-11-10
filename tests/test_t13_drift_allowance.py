"""T-13: narrow, recorded drift allowance for declared model dependencies."""
import json
import sqlite3

from test_t6_drift import _post, _findings, _seed, _span, _kv, client, llm_calls  # noqa: F401

AID = "act:test:t6"


def _declare(db_env, aid=AID):
    con = sqlite3.connect(str(db_env))
    con.execute("insert into dependencies (agent_id, kind, name, target, record) values (?,?,?,?,?)",
                (aid, "model", "break_classifier", "registry", json.dumps({})))
    con.commit()
    con.close()


def _cspan(i, name, args, trace, marker):
    s = _span(i, name, args, trace)
    if marker:
        s["attributes"].append(_kv("act.classifier.source", "local"))
    return s


def _reasons(client):
    return {(v["tool"], v["reason"]) for f in _findings(client, AID) for v in f["detail"]["violations"]}


def test_ac_declared_model_with_marker_is_not_violation(client, db_env):
    _seed(db_env, 2)
    _declare(db_env)
    _post(client, [_cspan(0, "break_classifier", {}, "e" * 32, True)])
    assert _findings(client, AID) == []


def test_ac_declared_model_name_without_marker_is_flagged(client, db_env):
    _seed(db_env, 2)
    _declare(db_env)
    _post(client, [_cspan(0, "break_classifier", {}, "f" * 32, False)])
    assert ("break_classifier", "tool_not_in_manifest") in _reasons(client)


def test_ac_undeclared_name_with_marker_still_flagged(client, db_env):
    _seed(db_env, 2)
    _declare(db_env)
    _post(client, [_cspan(0, "other_tool", {}, "1" * 32, True)])
    assert ("other_tool", "tool_not_in_manifest") in _reasons(client)


def test_ac_value_limit_on_declared_name_still_fires(client, db_env):
    _seed(db_env, 2, limits={"break_classifier": 100})
    _declare(db_env)
    _post(client, [_cspan(0, "break_classifier", {"amount": 5000}, "2" * 32, True)])
    assert ("break_classifier", "value_limit_exceeded") in _reasons(client)
