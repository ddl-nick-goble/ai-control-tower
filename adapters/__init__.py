"""Source adapters: one per agent platform, resolved by class through a small registry."""
import importlib

# provider -> (module, class). Snowflake has no adapter yet.
REGISTRY = {
    "aws": ("adapters.aws", "AwsAdapter"),
    "google": ("adapters.google", "GoogleAdapter"),
    "microsoft": ("adapters.microsoft", "MicrosoftAdapter"),
    "databricks": ("adapters.databricks", "DatabricksAdapter"),
    "domino": ("adapters.domino", "DominoAdapter"),
}


def get_adapter(provider: str):
    """An adapter instance for `provider`, or None when there is none. Only a missing adapter module counts as
    'none'; an import error inside the adapter (a missing dependency) is a real bug and propagates."""
    entry = REGISTRY.get(provider)
    if entry is None:
        return None
    module, cls = entry
    try:
        mod = importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name == module:
            return None
        raise
    return getattr(mod, cls)()
