"""Sample tenants: realistic permission checklists and scope trees per cloud, from fixtures/sample_tenants/."""
import copy
import json
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "sample_tenants"


def tenant(provider: str) -> dict:
    return json.loads((DIR / f"{provider}.json").read_text())


def permissions(provider: str, recheck: bool) -> list:
    """The checklist. The first check shows the one flagged item as missing; a re-check turns it green."""
    out = []
    for p in tenant(provider)["permissions"]:
        missing = bool(p.get("missing_first")) and not recheck
        out.append({"name": p["name"], "detail": p.get("detail", ""), "required": bool(p.get("required")),
                    "ok": not missing, "fix": p["fix"] if missing else None})
    return out


def tree(provider: str) -> list:
    return copy.deepcopy(tenant(provider)["tree"])
