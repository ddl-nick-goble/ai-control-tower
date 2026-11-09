"""Train break_classifier (tiny LogisticRegression on synthetic break features).

As __main__ (a Domino job): logs to MLflow, registers "break_classifier", alias "champion".
"""
import os
import sys

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


def main(output_dir="/tmp/break_classifier"):
    import mlflow
    import pandas as pd
    from mlflow.models import infer_signature
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from agents.recon_agent.experiment_setup import domino_tags, setup_experiment
    setup_experiment("break-classifier")
    model, X, y = _fit()
    df = pd.DataFrame(X[:5], columns=FEATURES)
    with mlflow.start_run(run_name="break-classifier-train"):
        mlflow.set_tags(domino_tags())
        mlflow.log_metric("train_accuracy", float(model.score(X, y)))
        info = mlflow.sklearn.log_model(model, "model", signature=infer_signature(df, model.predict(df)),
                                        input_example=df, registered_model_name="break_classifier")
    client = mlflow.MlflowClient()
    ver = max(int(v.version) for v in client.search_model_versions("name='break_classifier'"))
    client.set_registered_model_alias("break_classifier", "champion", str(ver))
    print("registered break_classifier v", ver, info.model_uri)


if __name__ == "__main__":
    main(*sys.argv[1:2])
