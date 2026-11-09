"""Microsoft Agent 365: Graph catalog packages."""
from .base import AdapterBase, SourceAgent, http_get_json, live_enabled, load_fixture


class MicrosoftAdapter(AdapterBase):
    platform = "microsoft"

    def _doc(self):
        if live_enabled("microsoft"):
            return http_get_json("microsoft", "/beta/copilot/admin/catalog/packages")
        return load_fixture("microsoft/list-packages.json")

    def list_agents(self):
        out = []
        for p in self._doc().get("value", []):
            md = p.get("customMetadata") or {}
            owner = md.get("owner") or (p.get("owner") or {}).get("userPrincipalName")
            out.append(SourceAgent(
                platform="microsoft", native_id=p["id"], name=p.get("agentName") or p.get("displayName") or p["id"],
                description=p.get("shortDescription") or "", version=str(p.get("version") or "1"),
                owner=owner, status="BLOCKED" if p.get("isBlocked") else "AVAILABLE",
                last_updated=p.get("lastModifiedDateTime"), provider=p.get("publisher") or "Microsoft",
                tools=list(md.get("tools") or []), data_scopes=list(md.get("data_scopes") or []),
                systems=list(md.get("systems") or []), value_limits=dict(md.get("value_limits") or {}),
                criteria=dict(md.get("criteria") or {}), identity=dict(p.get("identity") or {}),
                versions=[{"version": str(p.get("version") or "1"), "created": p.get("createdDateTime")}],
                extra={"display_name": p.get("displayName"), "hosts": p.get("supportedHosts")}, raw=p))
        return out
