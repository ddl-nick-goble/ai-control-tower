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
            import pandas as pd
            if isinstance(model_input, dict):  # harness passes {"data": {...}} as a dict of scalars
                model_input = model_input.get("data", model_input)
                model_input = [model_input] if isinstance(model_input, dict) and not any(
                    isinstance(v, (list, tuple)) for v in model_input.values()) else model_input
            df = pd.DataFrame(model_input)
            x = df.reindex(columns=FEATURES).apply(pd.to_numeric, errors="coerce").fillna(0.0)
            return self.model.predict(x.to_numpy(dtype="float64"))

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
