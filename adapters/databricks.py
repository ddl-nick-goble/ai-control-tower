"""Databricks Unity Catalog agent services."""
from datetime import datetime, timezone

from .base import AdapterBase, SourceAgent, http_get_json, live_enabled, load_fixture


def _ms(v):
    return datetime.fromtimestamp(v / 1000, timezone.utc).isoformat() if isinstance(v, (int, float)) else v


class DatabricksAdapter(AdapterBase):
    platform = "databricks"

    def _doc(self):
        if live_enabled("databricks"):
            return http_get_json("databricks", "/api/2.1/unity-catalog/agent-services")
        return load_fixture("databricks/agent-services.json")

    def list_agents(self):
        out = []
        for s in self._doc().get("agent_services", []):
            props = s.get("properties") or {}
            out.append(SourceAgent(
                platform="databricks", native_id=s["agent_service_id"], name=s.get("name") or s["agent_service_id"],
                description=s.get("comment") or "", version="1",
                owner=s.get("owner") or props.get("owner"), status="READY", last_updated=_ms(s.get("updated_at")),
                provider="Databricks", tools=list(props.get("tools") or []),
                data_scopes=list(props.get("data_scopes") or []), systems=list(props.get("systems") or []),
                value_limits=dict(props.get("value_limits") or {}), criteria=dict(props.get("criteria") or {}),
                identity=dict(s.get("identity") or {}),
                versions=[{"version": "1", "created": _ms(s.get("created_at"))}],
                extra={"full_name": s.get("full_name"), "endpoint": s.get("endpoint_name")}, raw=s))
        return out
