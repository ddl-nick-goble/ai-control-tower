"""MLflow tracing for the recon agent via the Domino agents SDK (@add_tracing).

The agent calls the gateway with requests (not the Anthropic SDK), so LLM calls are recorded as
spans by agents/recon_agent/act_export.py; @add_tracing wraps the agent entry point.
When domino.agents or mlflow cannot be imported, add_tracing is a no-op and agent_run yields None.
MLflow problems (no tracking server) never fail the agent run.

    from tracing_setup import add_tracing, agent_run
    with agent_run() as run:      # sets experiment act-recon-<project>-<user>, starts an agent run
        traced_fn(...)
"""
import functools
import logging
import os
from contextlib import contextmanager

log = logging.getLogger(__name__)

try:
    from domino.agents.logging import DominoAgentContext
    from domino.agents.tracing import add_tracing as _domino_add_tracing
    from experiment_setup import setup_experiment
except Exception:  # noqa: BLE001
    DominoAgentContext = _domino_add_tracing = setup_experiment = None

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "recon_agent", "config.yaml")
BASE = "act-recon"


def add_tracing(name, **kw):
    """Domino @add_tracing, or a no-op. A tracing failure re-runs nothing twice: errors raised by
    the wrapped function propagate; only setup failures fall back to an untraced call."""
    def deco(fn):
        if _domino_add_tracing is None:
            return fn
        started = {}

        @functools.wraps(fn)
        def inner(*a, **k):
            started["v"] = True
            try:
                started["result"] = fn(*a, **k)
                started["done"] = True
                return started["result"]
            except Exception as e:  # noqa: BLE001
                started["err"] = e
                raise

        traced = _domino_add_tracing(name=name, **kw)(inner)

        @functools.wraps(fn)
        def wrapper(*a, **k):
            started.clear()
            try:
                return traced(*a, **k)
            except Exception as e:  # noqa: BLE001
                if started.get("done"):  # fn finished; the tracer failed afterwards
                    log.warning("tracing failed after completion (%s); returning result", type(e).__name__)
                    return started["result"]
                if started.get("v"):
                    raise
                log.warning("tracing failed (%s); running untraced", type(e).__name__)
                return fn(*a, **k)
        return wrapper
    return deco


def span_evaluator(span):
    """Numeric score only when the output carries one; never invent defaults."""
    out = span.outputs if isinstance(span.outputs, dict) else {}
    return {"output_chars": float(len(str(out.get("text", out))))} if span.outputs is not None else {}


@contextmanager
def agent_run(summary_metrics=None, config_path=CONFIG):
    """Set experiment act-recon-<project>-<user>, then yield the Domino agent MLflow run
    (None when the SDK or tracking server is unavailable)."""
    ctx = None
    if DominoAgentContext is not None:
        try:
            setup_experiment(BASE)
            ctx = DominoAgentContext(agent_config_path=config_path,
                                     custom_summary_metrics=summary_metrics or [])
            run = ctx.__enter__()
        except Exception as e:  # noqa: BLE001
            log.warning("agent run setup failed (%s); continuing without an MLflow run", type(e).__name__)
            ctx = run = None
    else:
        run = None
    try:
        yield run
    except BaseException:
        import sys
        if ctx is not None and not ctx.__exit__(*sys.exc_info()):
            raise
        if ctx is None:
            raise
    else:
        if ctx is not None:
            try:
                ctx.__exit__(None, None, None)
            except Exception as e:  # noqa: BLE001
                log.warning("agent run close failed (%s)", type(e).__name__)
