"""Train break_classifier (tiny LogisticRegression on synthetic break features).

As __main__ (a Domino job): logs to MLflow, registers "break_classifier", alias "champion".
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression

FEATURES = ["amount_diff", "days_diff", "missing_side"]
MODEL_FILE = "break_classifier.joblib"


def validate_features(payload):
    """Validate a payload into (float64 ndarray [n, len(FEATURES)], is_list). Raises ValueError.

    Shared by the pyfunc wrapper and predict.py. Self-contained (local imports) so it survives
    being pickled by value into the logged model.
    """
    import numpy as np
    import pandas as pd
    feats = ["amount_diff", "days_diff", "missing_side"]
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], (dict, list)):
        payload = payload["data"]
    is_list = not isinstance(payload, dict)
    if isinstance(payload, pd.DataFrame):
        df = payload
    elif isinstance(payload, dict):
        if not payload:
            raise ValueError("empty input")
        if any(isinstance(v, (list, tuple)) for v in payload.values()):
            df = pd.DataFrame(payload)
            is_list = True
        else:
            df = pd.DataFrame([payload])
    elif isinstance(payload, (list, tuple)):
        if len(payload) == 0:
            raise ValueError("empty input")
        df = pd.DataFrame(list(payload))
    else:
        raise ValueError("unsupported input type %s" % type(payload).__name__)
    if len(df) == 0:
        raise ValueError("empty input")
    missing = [f for f in feats if f not in df.columns]
    if missing:
        raise ValueError("missing required feature(s): %s" % ", ".join(missing))
    x = df[feats]
    try:
        x = x.apply(pd.to_numeric, errors="raise")
    except (ValueError, TypeError) as e:
        raise ValueError("non-numeric feature value: %s" % e) from e
    arr = x.to_numpy(dtype="float64")
    if not np.isfinite(arr).all():
        raise ValueError("feature values must be finite (no NaN/inf)")
    return arr, is_list


def _data(n=300, seed=0):
    r = np.random.default_rng(seed)
    t = np.c_[np.zeros(n), r.integers(1, 6, n), np.zeros(n)]
    a = np.c_[r.uniform(1, 500, n), np.zeros(n), np.zeros(n)]
    m = np.c_[r.uniform(0, 2000, n), np.zeros(n), np.ones(n)]
    X = np.vstack([t, a, m])
    y = ["timing"] * n + ["amount_mismatch"] * n + ["missing_entry"] * n
    return X, np.array(y)


def _fit():
    X, y = _data()
    return LogisticRegression(max_iter=1000).fit(X, y), X, y


def train(output_dir: str) -> str:
    model, _, _ = _fit()
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, MODEL_FILE)
    joblib.dump(model, path)
    return path


def _wrapper_cls():
    import mlflow.pyfunc

    class BreakClassifierPyfunc(mlflow.pyfunc.PythonModel):
        """Coerces every feature to float64 so JSON ints and floats are both accepted.

        MLflow schema enforcement rejects an int64 column when the signature says double
        (HTTP 400 "Can not safely convert int64 to float64"), so the logged model carries no
        enforcing column schema and casting happens here instead.
        """

        def load_context(self, context):
            self.model = joblib.load(context.artifacts["model"])

        def predict(self, context, model_input, params=None):
            x, is_list = validate_features(model_input)
            labels = [str(c) for c in self.model.predict(x)]
            out = [{"class": c} for c in labels]
            return out if is_list else out[0]

    return BreakClassifierPyfunc


def main(output_dir="/tmp/break_classifier"):
    import mlflow
    from experiment_setup import log_domino_context, setup_experiment
    setup_experiment("break-classifier")
    model, X, y = _fit()
    path = train(output_dir)
    with mlflow.start_run(run_name="break-classifier-train"):
        log_domino_context()
        mlflow.log_metric("train_accuracy", float(model.score(X, y)))
        info = mlflow.pyfunc.log_model(
            "model", python_model=_wrapper_cls()(), artifacts={"model": path},
            pip_requirements=["mlflow", "scikit-learn", "joblib", "pandas", "numpy"],
            registered_model_name="break_classifier")
    client = mlflow.MlflowClient()
    ver = max(int(v.version) for v in client.search_model_versions("name='break_classifier'"))
    client.set_registered_model_alias("break_classifier", "champion", str(ver))
    print("registered break_classifier v", ver, info.model_uri)


if __name__ == "__main__":
    main(*sys.argv[1:2])
