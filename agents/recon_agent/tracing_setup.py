"""Tracing helpers: Domino @add_tracing when available, otherwise a no-op decorator.
MLflow problems (no tracking server) never fail the agent run."""
import functools
import logging

log = logging.getLogger(__name__)

try:
    from domino.agents.tracing import add_tracing as _domino_add_tracing
except Exception:  # noqa: BLE001
    _domino_add_tracing = None


def add_tracing(name, **kw):
    def deco(fn):
        if _domino_add_tracing is None:
            return fn
        traced = _domino_add_tracing(name=name, **kw)(fn)

        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return traced(*a, **k)
            except Exception as e:  # noqa: BLE001
                if getattr(e, "_agent_error", False):
                    raise
                log.warning("tracing failed (%s); running untraced", type(e).__name__)
                return fn(*a, **k)
        return wrapper
    return deco
