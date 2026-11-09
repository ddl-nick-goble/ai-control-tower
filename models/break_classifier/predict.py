"""Endpoint entry function: predict(payload) -> {"class": str}. Model path from BREAK_CLASSIFIER_MODEL_PATH."""
import os

import joblib

_cache = {}
FEATURES = ["amount_diff", "days_diff", "missing_side"]


def _model():
    p = os.environ["BREAK_CLASSIFIER_MODEL_PATH"]
    if p not in _cache:
        _cache[p] = joblib.load(p)
    return _cache[p]


def predict(payload: dict = None, **kw) -> dict:
    d = payload if payload is not None else kw
    d = d.get("data", d)
    x = [[float(d.get(f, 0)) for f in FEATURES]]
    return {"class": str(_model().predict(x)[0])}
