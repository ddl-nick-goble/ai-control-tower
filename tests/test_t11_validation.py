"""T-11 tests: validation job and record (AC-10).

Assumed contracts (builder must match):
- eval/prompts.jsonl: >= 20 lines of {"id", "prompt", optional "expected"}.
- jobs.validate.run_validation(prompts, reps=5, agent_run=None, judge=None, mlflow_client=None,
  threshold=0.7) -> dict with keys: mean, p5, p95, pass_rate, n_prompts, reps, threshold,
  judge_alias, judge_model_id, mlflow_run_ids (list, one per repetition), experiment, scores.
  * agent_run(prompt: str) -> dict (or str); judge(prompt, response, expected) ->
    {"score": float, "model": gateway-reported model id}. Percentiles use linear interpolation
    over all prompt x rep scores (numpy default / statistics inclusive).
  * mlflow_client has the mlflow module surface: set_experiment(name), start_run(run_name=...)
    as a context manager yielding an object with .info.run_id, log_param, log_metric.
  * experiment name = <base>-<DOMINO_PROJECT_NAME>-<DOMINO_STARTING_USERNAME>, base starts
    with "validate" or "act-validate".
  * judge_alias is "sonnet".
- jobs.validate.post_validation(agent_id, result) POSTs JSON to <ACT_URL>/api/agents/<id>/validation
  with Authorization: Bearer <token>, token read via requests.get(ACT_TOKEN_URL).
- POST /api/agents/{id}/validation body = the result dict (plus threshold); stored result is
  "pass" if mean >= threshold else "fail". 422 if n_prompts < 20 or reps < 5.
- GET /api/agents/{id}/validation returns the latest record (dict with kind/result/detail).
"""
import contextlib
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

from test_t10_evidence import fake, evdir, client, _agent, _pack, _section, ALICE  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
RAW_MODEL = "claude-sonnet-4-5-20250929"


def _prompts(n=20):
    return [{"id": f"p{i}", "prompt": f"prompt {i}", "expected": f"e{i}"} for i in range(n)]


class FakeMlflow:
    def __init__(self):
        self.experiments, self.runs, self._cur = [], [], None

    def set_experiment(self, name):
        self.experiments.append(name)

    @contextlib.contextmanager
    def start_run(self, run_name=None, **kw):
        run = {"id": f"run{len(self.runs)}", "name": run_name, "params": {}, "metrics": [],
               "experiment": self.experiments[-1] if self.experiments else None}
        self.runs.append(run)
        self._cur = run
        yield type("R", (), {"info": type("I", (), {"run_id": run["id"]})()})()
        self._cur = None

    def log_param(self, k, v):
        self._cur["params"][k] = v

    def log_params(self, d):
        self._cur["params"].update(d)

    def log_metric(self, k, v, step=None):
        self._cur["metrics"].append((k, v))

    def log_metrics(self, d, step=None):
        self._cur["metrics"].extend(d.items())

    def set_tag(self, *a, **k):
        pass

    def log_dict(self, *a, **k):
        pass

    def log_text(self, *a, **k):
        pass


def _score_by_index(prompt, response, expected):
    i = int(prompt.split()[-1])
    return {"score": (i + 1) / 20, "model": RAW_MODEL}


def _run(monkeypatch, prompts=None, judge=_score_by_index, mlf=None, **kw):
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "projx")
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "alice")
    v = importlib.import_module("jobs.validate")
    mlf = mlf or FakeMlflow()
    calls = []

    def agent_run(prompt):
        calls.append(prompt)
        return {"output": f"answer to {prompt}"}

    res = v.run_validation(prompts or _prompts(), reps=kw.pop("reps", 5), agent_run=agent_run,
                           judge=judge, mlflow_client=mlf, **kw)
    return res, mlf, calls


def test_ac10_eval_prompts_file():
    p = ROOT / "eval" / "prompts.jsonl"
    assert p.is_file()
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    assert len(rows) >= 20
    for r in rows:
        assert r["id"] and r["prompt"]
    assert len({r["id"] for r in rows}) == len(rows)


def test_ac10_runs_every_prompt_every_rep(monkeypatch):
    _, _, calls = _run(monkeypatch)
    assert len(calls) == 100
    assert all(calls.count(f"prompt {i}") == 5 for i in range(20))


def test_ac10_one_mlflow_run_per_repetition(monkeypatch):
    res, mlf, _ = _run(monkeypatch)
    assert len(mlf.runs) == 5
    assert len(res["mlflow_run_ids"]) == 5 and set(res["mlflow_run_ids"]) == {r["id"] for r in mlf.runs}
    reps = {str(r["params"].get("rep", r["params"].get("rep_index", r["params"].get("repetition"))))
            for r in mlf.runs}
    assert len(reps) == 5 and "None" not in reps
    for r in mlf.runs:
        assert "sonnet" in json.dumps(r["params"])
        assert r["metrics"], "run logged no scores"


def test_ac10_experiment_name_shape(monkeypatch):
    res, mlf, _ = _run(monkeypatch)
    assert re.fullmatch(r"(act-)?validate[\w-]*-projx-alice", res["experiment"]), res["experiment"]
    assert set(mlf.experiments) == {res["experiment"]}


def test_ac10_distribution_maths(monkeypatch):
    res, _, _ = _run(monkeypatch)
    # scores 0.05..1.00 each x5 -> 100 values
    assert res["mean"] == pytest.approx(0.525)
    assert res["p5"] == pytest.approx(0.0975)
    assert res["p95"] == pytest.approx(0.9525)
    assert res["pass_rate"] == pytest.approx(0.35)  # 0.70..1.00 = 7 prompts of 20
    assert res["n_prompts"] == 20 and res["reps"] == 5


def test_ac10_pass_rate_threshold_configurable(monkeypatch):
    res, _, _ = _run(monkeypatch, threshold=0.5)
    assert res["pass_rate"] == pytest.approx(0.55)  # 0.50..1.00 = 11 of 20


def test_ac10_judge_alias_and_gateway_model_recorded(monkeypatch):
    res, _, _ = _run(monkeypatch)
    assert res["judge_alias"] == "sonnet"
    assert res["judge_model_id"] == RAW_MODEL


def _dist(**over):
    d = {"mean": 0.825, "p5": 0.4, "p95": 0.95, "pass_rate": 0.8, "n_prompts": 20, "reps": 5,
         "threshold": 0.7, "judge_alias": "sonnet", "judge_model_id": RAW_MODEL,
         "mlflow_run_ids": [f"r{i}" for i in range(5)], "experiment": "validate-projx-alice"}
    d.update(over)
    return d


def _post(client, aid, body, headers=ALICE):
    return client.post(f"/api/agents/{aid}/validation", json=body, headers=headers)


def test_ac10_post_stores_record(client):
    aid = _agent(client)
    r = _post(client, aid, _dist())
    assert r.status_code in (200, 201), r.text
    from backend import db
    with db.engine.connect() as cx:
        rows = cx.execute(db.validation_records.select().where(db.validation_records.c.agent_id == aid)).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "repeated_eval" and row.result == "pass"
    d = row.detail
    d = json.loads(d) if isinstance(d, str) else d
    for k in ("mean", "p5", "p95", "pass_rate", "n_prompts", "reps", "judge_alias", "judge_model_id",
              "mlflow_run_ids", "experiment"):
        assert d[k] == _dist()[k], k
    g = client.get(f"/api/agents/{aid}/validation")
    assert g.status_code == 200 and g.json()["detail"]["mean"] == 0.825


def test_ac10_result_fail_below_threshold_and_latest_returned(client):
    aid = _agent(client)
    assert _post(client, aid, _dist()).status_code in (200, 201)
    assert _post(client, aid, _dist(mean=0.4, pass_rate=0.3)).status_code in (200, 201)
    g = client.get(f"/api/agents/{aid}/validation").json()
    assert g["result"] == "fail" and g["detail"]["mean"] == 0.4


def test_ac10_validation_requires_identity(client):
    aid = _agent(client)
    assert client.post(f"/api/agents/{aid}/validation", json=_dist()).status_code == 401
    assert _post(client, aid, _dist()).status_code in (200, 201)  # route exists for identified user


def test_ac10_unknown_agent_404(client):
    assert _post(client, _agent(client), _dist()).status_code in (200, 201)  # route exists
    assert _post(client, "no-such-agent", _dist()).status_code == 404


def test_ac10_rejects_under_20_prompts_or_5_reps(client):
    aid = _agent(client)
    assert _post(client, aid, _dist(n_prompts=19)).status_code == 422
    assert _post(client, aid, _dist(reps=4)).status_code == 422


def test_ac10_evidence_pack_includes_validation_record(client):
    aid = _agent(client)
    assert _post(client, aid, _dist()).status_code in (200, 201)
    rid = client.get(f"/api/agents/{aid}/validation").json()["id"]
    r = client.post(f"/api/agents/{aid}/evidence-pack", headers=ALICE)
    assert r.status_code == 200, r.text
    sec = _section(_pack(client, aid), "validation")
    text = json.dumps(sec["content"])
    low = text.lower()
    assert "no validation record" not in low
    assert "0.825" in text and "0.400" in text and "0.950" in text
    assert "80.0%" in text or "0.800" in text or "0.8" in text
    assert "sonnet" in low
    assert f"validation_records:{rid}" in json.dumps(sec["citations"])
    assert "claude" not in low
    assert "claude" not in Path(r.json()["markdown_path"]).read_text().lower()


def test_ac10_detail_keeps_raw_gateway_model_id(client):
    aid = _agent(client)
    assert _post(client, aid, _dist()).status_code in (200, 201)
    assert client.get(f"/api/agents/{aid}/validation").json()["detail"]["judge_model_id"] == RAW_MODEL


def test_ac10_job_posts_with_bearer_token_to_act(monkeypatch):
    import requests
    monkeypatch.setenv("ACT_URL", "http://act.invalid")
    monkeypatch.setenv("ACT_TOKEN_URL", "http://tok.invalid/access-token")
    posts = []

    class Resp:
        status_code = 200
        text = "stub-token"

        def json(self):
            return {}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda u, *a, **k: Resp())
    monkeypatch.setattr(requests, "post", lambda u, *a, **k: (posts.append((u, k)), Resp())[1])
    monkeypatch.setattr(requests.Session, "post",
                        lambda self, u, *a, **k: (posts.append((u, k)), Resp())[1])
    v = importlib.import_module("jobs.validate")
    v.post_validation("agent-9", _dist())
    assert posts, "nothing posted"
    url, kw = posts[0]
    assert url == "http://act.invalid/api/agents/agent-9/validation"
    assert kw["headers"]["Authorization"] == "Bearer stub-token"
    assert kw.get("json", {}).get("mean") == 0.825 or "0.825" in str(kw.get("data"))
