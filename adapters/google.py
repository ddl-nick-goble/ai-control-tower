"""Google Agent Registry: Service + Binding resources."""
from .base import AdapterBase, SourceAgent, http_get_json, live_enabled, load_fixture


class GoogleAdapter(AdapterBase):
    platform = "google"

    def _docs(self):
        if live_enabled("google"):
            return (http_get_json("google", "/services"), http_get_json("google", "/bindings"))
        return (load_fixture("google/agent-registry-services.json"),
                load_fixture("google/agent-registry-bindings.json"))

    def list_agents(self):
        services, bindings = self._docs()
        by_svc = bindings.get("bindingsByService", {})
        out = []
        for s in services.get("services", []):
            props = s.get("properties") or {}
            short = s["name"].rsplit("/", 1)[-1]
            engine, sa = None, None
            for b in by_svc.get(s["name"], []):
                eng = (b.get("target") or {}).get("agentEngine")
                if eng and not engine:
                    engine = eng.rsplit("/", 1)[-1]
                sa = sa or (b.get("authProviderBinding") or {}).get("serviceAccount")
            ident = dict(s.get("identity") or {})
            if ident.get("type") != "none" and not ident.get("principal") and sa:
                ident["principal"] = sa
            iface = (s.get("interfaces") or [{}])[0]
            out.append(SourceAgent(
                platform="google", native_id=engine or short, name=s.get("displayName") or short,
                description=s.get("description") or "", version="1",
                owner=props.get("owner") or props.get("owner_email"), status="READY",
                last_updated=s.get("updateTime"), url=iface.get("url"), provider="Google Cloud",
                tools=list(props.get("tools") or []), data_scopes=list(props.get("data_scopes") or []),
                systems=list(props.get("systems") or []), value_limits=dict(props.get("value_limits") or {}),
                criteria=dict(props.get("criteria") or {}), identity=ident,
                versions=[{"version": "1", "created": s.get("createTime")}],
                extra={"service": s["name"], "bindings": len(by_svc.get(s["name"], []))},
                raw={"service": s, "bindings": by_svc.get(s["name"], [])}))
        return out
