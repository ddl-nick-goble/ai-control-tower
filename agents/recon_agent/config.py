"""Settings read path for the recon agent (T-39, ST-4).

The recon agent runs as its own process, so it cannot share the app's memory. It reads each setting through the same
accessor the app uses (backend.settings.get_setting) against the same ACT database: a Domino Project or User variable
still wins (ST-3), otherwise the value saved on the Settings page applies, within a few seconds and without a restart.
If the accessor cannot be loaded (for example a stripped-down deployment without the backend package) the
environment variable alone is used.
"""
import os

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
    except Exception:  # noqa: BLE001
        v = os.environ.get(_ENV[name])
        return v if v else default
