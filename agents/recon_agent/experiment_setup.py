"""MLflow experiment naming: {base}-{project}-{username} (unique across the deployment)."""
import os


def experiment_name(base: str) -> str:
    return f"{base}-{os.environ.get('DOMINO_PROJECT_NAME', 'local')}-{os.environ.get('DOMINO_STARTING_USERNAME', 'dev')}"


def setup_experiment(base: str) -> str:
    import mlflow
    name = experiment_name(base)
    mlflow.set_experiment(name)
    return name


def domino_tags() -> dict:
    return {"domino.project": os.environ.get("DOMINO_PROJECT_NAME", ""),
            "domino.run_id": os.environ.get("DOMINO_RUN_ID", "")}
