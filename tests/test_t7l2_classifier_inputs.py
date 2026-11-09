"""T-7L2: break_classifier input contracts (fail loudly on bad input, never a confident guess)."""
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from models.break_classifier import train as train_mod  # noqa: E402


@pytest.fixture(scope="module")
def model_path(tmp_path_factory):
    return train_mod.train(str(tmp_path_factory.mktemp("bc")))


@pytest.fixture()
def wrapper(model_path):
    w = train_mod._wrapper_cls()()
    ctx = SimpleNamespace(artifacts={"model": model_path})
    w.load_context(ctx)
    return lambda inp: w.predict(ctx, inp)


def _is_cls(r):
    return isinstance(r, dict) and set(r) == {"class"} and isinstance(r["class"], str)


# ---- 1. pyfunc wrapper
def test_ac1_wrapper_ints(wrapper):
    assert _is_cls(wrapper({"amount_diff": 12, "days_diff": 2, "missing_side": 0}))


def test_ac1_wrapper_floats(wrapper):
    assert _is_cls(wrapper({"amount_diff": 12.5, "days_diff": 2.0, "missing_side": 0.0}))


def test_ac1_wrapper_mixed(wrapper):
    assert _is_cls(wrapper({"amount_diff": 12.5, "days_diff": 2, "missing_side": 1.0}))


def test_ac1_wrapper_list_of_two_returns_list(wrapper):
    rows = [{"amount_diff": 12, "days_diff": 2, "missing_side": 0},
            {"amount_diff": 1200.0, "days_diff": 0, "missing_side": 1}]
    out = wrapper(rows)
    assert isinstance(out, list) and len(out) == 2 and all(_is_cls(r) for r in out)


def test_ac1_wrapper_list_of_one_returns_list(wrapper):
    out = wrapper([{"amount_diff": 12, "days_diff": 2, "missing_side": 0}])
    assert isinstance(out, list) and len(out) == 1 and _is_cls(out[0])


def test_ac1_wrapper_missing_days_diff_raises_naming_it(wrapper):
    with pytest.raises(ValueError, match="days_diff"):
        wrapper({"amount_diff": 12, "missing_side": 0})


def test_ac1_wrapper_non_numeric_raises(wrapper):
    with pytest.raises(ValueError):
        wrapper({"amount_diff": "abc", "days_diff": 2, "missing_side": 0})


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_ac1_wrapper_non_finite_raises(wrapper, bad):
    with pytest.raises(ValueError):
        wrapper({"amount_diff": bad, "days_diff": 2, "missing_side": 0})


def test_ac1_wrapper_empty_list_raises(wrapper):
    with pytest.raises(ValueError):
        wrapper([])


# ---- 2. predict.predict
@pytest.fixture()
def predict_fn(model_path, monkeypatch):
    monkeypatch.setenv("BREAK_CLASSIFIER_MODEL_PATH", model_path)
    from models.break_classifier.predict import predict
    return predict


def test_ac2_predict_valid_ints(predict_fn):
    assert _is_cls(predict_fn({"amount_diff": 12, "days_diff": 2, "missing_side": 0}))


def test_ac2_predict_missing_feature_raises_naming_it(predict_fn):
    with pytest.raises(ValueError, match="days_diff"):
        predict_fn({"amount_diff": 12, "missing_side": 0})


def test_ac2_predict_non_numeric_raises(predict_fn):
    with pytest.raises(ValueError):
        predict_fn({"amount_diff": "abc", "days_diff": 2, "missing_side": 0})


# ---- 3. diag script
DIAG = os.path.join(ROOT, "scripts", "diag_break_endpoint.py")


def test_ac3_diag_requires_url():
    env = {k: v for k, v in os.environ.items()
           if k not in ("BREAK_CLASSIFIER_ENDPOINT_URL", "BREAK_CLASSIFIER_ACCESS_KEY")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["BREAK_CLASSIFIER_ACCESS_KEY"] = "dummy"
    p = subprocess.run([sys.executable, DIAG], env=env, capture_output=True, text=True,
                       timeout=60, cwd=ROOT)
    assert p.returncode != 0
    assert "BREAK_CLASSIFIER_ENDPOINT_URL" in (p.stdout + p.stderr)


def test_ac3_diag_no_hardcoded_domino_host():
    with open(DIAG) as f:
        assert not re.search(r"domino\.tech", f.read())
