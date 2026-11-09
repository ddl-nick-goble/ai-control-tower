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
    from models.break_classifier.train import validate_features
    x, is_list = validate_features(payload if payload is not None else kw)
    out = [{"class": str(c)} for c in _model().predict(x)]
    return out if is_list else out[0]
