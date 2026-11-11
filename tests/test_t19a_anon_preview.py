"""T-19a P-1: tier preview works without identity; registration still guarded."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient

HDR = {"X-Domino-User-Id": "u19", "X-Domino-User": "anon-tester"}
NAMES = ["decision_impact", "financial_exposure", "customer_impact", "autonomy",
         "data_sensitivity", "reversibility", "regulatory_exposure"]
ALL3 = {k: 3 for k in NAMES}
URL = "/api/tiering/preview"


@pytest.fixture
def client(db_env, monkeypatch):
    monkeypatch.setenv("ACT_LLM_PROVIDER", "mock")
    for m in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[m]
    main = importlib.import_module("backend.main")
    with TestClient(main.app) as c:
        yield c


def test_p1_anonymous_preview_200_all_threes(client):
    r = client.post(URL, json={"criteria": ALL3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"tier", "score", "reasoning"} <= set(body)
    assert body["tier"] == 2
    assert body["score"] == pytest.approx(3.0)
    assert body["reasoning"]


def test_p1_anonymous_preview_empty_criteria_422(client):
    assert client.post(URL, json={"criteria": {}}).status_code == 422


@pytest.mark.parametrize("bad", [0, 6, 3.5, "abc"])
def test_p1_anonymous_preview_bad_value_422(client, bad):
    r = client.post(URL, json={"criteria": {**ALL3, "autonomy": bad}})
    assert r.status_code == 422


def test_p1_anonymous_preview_missing_criteria_422(client):
    assert client.post(URL, json={}).status_code == 422


def test_p1_signed_in_preview_still_200(client):
    r = client.post(URL, json={"criteria": ALL3}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == 2


def test_p1_anonymous_registration_still_401(client):
    r = client.post("/api/agents/external", json={"name": "x"})
    assert r.status_code == 401
