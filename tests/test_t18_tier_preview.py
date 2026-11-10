"""T-18 tests: POST /api/tiering/preview (R-5), read-only tier preview via score_tier()."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

HDR = {"X-Domino-User-Id": "u18", "X-Domino-User": "preview-tester"}
NAMES = ["decision_impact", "financial_exposure", "customer_impact", "autonomy",
         "data_sensitivity", "reversibility", "regulatory_exposure"]
ALL5 = {k: 5 for k in NAMES}
ALL1 = {k: 1 for k in NAMES}
MIXED = dict(zip(NAMES, [5, 4, 3, 2, 1, 3, 4]))
MIXED2 = dict(zip(NAMES, [3, 3, 2, 3, 2, 2, 3]))
URL = "/api/tiering/preview"


@pytest.fixture
def client(db_env, monkeypatch):
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


def _expected(criteria):
    tiering = importlib.import_module("backend.tiering")
    r = tiering.score_tier(criteria)
    return r.tier, r.score


def _post(client, criteria, headers=HDR):
    return client.post(URL, json={"criteria": criteria}, headers=headers)


@pytest.mark.parametrize("criteria", [ALL5, ALL1, MIXED, MIXED2])
def test_ac_r5_matches_score_tier(client, criteria):
    r = _post(client, criteria)
    assert r.status_code == 200, r.text
    body = r.json()
    tier, score = _expected(criteria)
    assert isinstance(body["tier"], int) and 1 <= body["tier"] <= 4
    assert body["tier"] == tier
    assert body["score"] == pytest.approx(score)


def test_ac_r5_extremes(client):
    assert _post(client, ALL5).json()["tier"] == 1
    assert _post(client, ALL1).json()["tier"] == 4


def test_ac_r5_accepts_mid_range_ints(client):
    assert _post(client, {k: 3 for k in NAMES}).status_code == 200


@pytest.mark.parametrize("bad", [0, 6, -1, 100])
def test_ac_r5_rejects_out_of_range(client, bad):
    assert _post(client, {**ALL5, "autonomy": bad}).status_code == 422


def test_ac_r5_rejects_missing_criterion(client):
    c = dict(ALL5)
    del c["reversibility"]
    assert _post(client, c).status_code == 422


def test_ac_r5_rejects_empty_criteria(client):
    assert _post(client, {}).status_code == 422


def test_ac_r5_rejects_unknown_key(client):
    assert _post(client, {**ALL5, "bogus_factor": 3}).status_code == 422


@pytest.mark.parametrize("bad", ["abc", None, [], {}])
def test_ac_r5_rejects_non_numeric(client, bad):
    assert _post(client, {**ALL5, "autonomy": bad}).status_code == 422


def test_ac_r5_rejects_non_integer_float(client):
    assert _post(client, {**ALL5, "autonomy": 3.5}).status_code == 422


@pytest.mark.parametrize("key", ["backtest_score", "benchmark_accuracy", "Benchmark"])
def test_ac_r5_no_benchmark_rule_422(client, key):
    r = _post(client, {**ALL5, key: 5})
    assert r.status_code == 422
    assert "no_benchmark_rule" in r.text


@pytest.mark.parametrize("body", [{}, {"criteria": "x"}, {"criteria": [1]}, [1, 2]])
def test_ac_r5_rejects_malformed_body(client, body):
    assert client.post(URL, json=body, headers=HDR).status_code == 422


def test_ac_r5_writes_nothing(client):
    db = importlib.import_module("backend.db")

    def snap():
        with db.engine.connect() as cx:
            return {t.name: [tuple(r) for r in cx.execute(select(t)).fetchall()]
                    for t in db.metadata.sorted_tables}
    before = snap()
    for c in (ALL5, ALL1, MIXED, {**ALL5, "autonomy": 9}, {**ALL5, "benchmark": 1}):
        _post(client, c)
    assert snap() == before


def test_ac_r5_anonymous_post_blocked_by_write_guard(client):
    r = client.post(URL, json={"criteria": ALL5})
    assert r.status_code == 401


def test_ac_r5_identified_user_gets_200(client):
    r = _post(client, ALL5, headers=HDR)
    assert r.status_code == 200
