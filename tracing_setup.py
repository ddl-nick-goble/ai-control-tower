"""MLflow tracing for the recon agent via the Domino agents SDK (@add_tracing).

The agent calls the gateway with requests (not the Anthropic SDK), so LLM calls are traced by
decorating create_message / tool functions with @add_tracing rather than by autologging.
ACT export is separate (agents/recon_agent/act_export.py).

    from tracing_setup import add_tracing, agent_run
    with agent_run() as run:      # sets the experiment, starts a Domino agent run
        traced_fn(...)
"""
import os
from contextlib import contextmanager

import mlflow
from domino.agents.logging import DominoAgentContext
from domino.agents.tracing import add_tracing  # noqa: F401  (re-exported)

from experiment_setup import setup_experiment

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "recon_agent", "config.yaml")
BASE = "act-recon"


def span_evaluator(span):
    """Numeric score only when the output carries one; never invent defaults."""
    out = span.outputs if isinstance(span.outputs, dict) else {}
    return {"output_chars": float(len(str(out.get("text", out))))} if span.outputs is not None else {}


@contextmanager
def agent_run(summary_metrics=None, config_path=CONFIG):
    """Set experiment act-recon-<project>-<user>, then yield the Domino agent MLflow run."""
    setup_experiment(BASE)
    with DominoAgentContext(agent_config_path=config_path,
                            custom_summary_metrics=summary_metrics or []) as run:
        yield run
