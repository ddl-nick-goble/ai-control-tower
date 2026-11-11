"""Settings read path for the recon agent (T-39, ST-4).

The recon agent runs as its own process, so it cannot share the app's memory. It reads each setting through the same
accessor the app uses (backend.settings.get_setting) against the same ACT database (a demo switch made in the app's own process is not visible here): a Domino Project or User variable
still wins (ST-3), otherwise the value saved on the Settings page applies, within a few seconds and without a restart.
If the accessor cannot be loaded (for example a stripped-down deployment without the backend package) the
environment variable alone is used.
"""
import logging
import os

log = logging.getLogger(__name__)
_warned = False

_ENV = {
    "llm_provider": "ACT_LLM_PROVIDER",
    "llm_gateway_url": "LLM_GATEWAY_URL",
    "break_classifier_endpoint_url": "BREAK_CLASSIFIER_ENDPOINT_URL",
    "break_classifier_access_key": "BREAK_CLASSIFIER_ACCESS_KEY",
}


def get(name, default=None):
    try:
        from backend.settings import get_setting
        return get_setting(name, default)
    except Exception as e:  # noqa: BLE001
        global _warned
        if not _warned:
            _warned = True
            log.warning("recon agent: settings accessor unavailable (%s); using environment variables only", type(e).__name__)
        v = os.environ.get(_ENV[name])
        return v if v else default
