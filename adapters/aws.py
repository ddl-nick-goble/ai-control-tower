"""AWS Bedrock AgentCore: ListAgentRuntimes + Agent Registry records."""
from .base import AdapterBase, SourceAgent, http_get_json, live_enabled, load_fixture


class AwsAdapter(AdapterBase):
    platform = "aws"

    def _docs(self):
        if live_enabled("aws"):
            return (http_get_json("aws", "/agent-runtimes"),
                    http_get_json("aws", "/discoverable-registry-records"))
        return (load_fixture("aws/list-agent-runtimes.json"),
                load_fixture("aws/list-discoverable-registry-records.json"))

    def list_agents(self):
        runtimes, registry = self._docs()
        recs = {r.get("descriptors", {}).get("agent", {}).get("runtimeArn"): r
                for r in registry.get("registryRecords", [])}
        out = []
        for rt in runtimes.get("agentRuntimes", []):
            md = rt.get("metadata") or {}
            rec = recs.get(rt.get("agentRuntimeArn")) or {}
            out.append(SourceAgent(
                platform="aws", native_id=rt["agentRuntimeId"], name=rt.get("agentRuntimeName") or rt["agentRuntimeId"],
                description=rt.get("description") or rec.get("description") or "",
                version=str(rt.get("agentRuntimeVersion") or rec.get("version") or "1"),
                owner=md.get("owner") or (rt.get("tags") or {}).get("owner"),
                status=rt.get("status", ""), last_updated=rt.get("lastUpdatedAt"),
                provider="AWS", tools=list(md.get("tools") or []), data_scopes=list(md.get("data_scopes") or []),
                systems=list(md.get("systems") or []), value_limits=dict(md.get("value_limits") or {}),
                criteria=dict(md.get("criteria") or {}), identity=dict(rt.get("identity") or {}),
                versions=[{"version": str(rt.get("agentRuntimeVersion") or "1"), "created": rt.get("lastUpdatedAt")}],
                extra={"arn": rt.get("agentRuntimeArn"), "registry_status": rec.get("status"),
                       "registry_record": rec.get("recordId")},
                raw={"runtime": rt, "registry": rec}))
        return out
