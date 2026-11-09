"""OTLP/HTTP request decoding (JSON and protobuf) into plain span dicts."""
import json


def _anyvalue(v: dict):
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return v["boolValue"]
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "arrayValue" in v:
        return [_anyvalue(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return attrs_to_dict(v["kvlistValue"].get("values", []))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def attrs_to_dict(attrs) -> dict:
    return {a.get("key"): _anyvalue(a.get("value", {})) for a in (attrs or []) if a.get("key")}


def _flatten(doc: dict) -> list[dict]:
    out = []
    for rs in doc.get("resourceSpans", []) or []:
        res = attrs_to_dict((rs.get("resource") or {}).get("attributes"))
        for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])) or []:
            for sp in ss.get("spans", []) or []:
                out.append({
                    "trace_id": sp.get("traceId", ""),
                    "span_id": sp.get("spanId", ""),
                    "parent_span_id": sp.get("parentSpanId") or None,
                    "name": sp.get("name", ""),
                    "start_ns": int(sp.get("startTimeUnixNano") or 0),
                    "end_ns": int(sp.get("endTimeUnixNano") or 0),
                    "attributes": attrs_to_dict(sp.get("attributes")),
                    "resource": res,
                    "status": sp.get("status"),
                })
    return out


def parse_json(body: bytes) -> list[dict]:
    return _flatten(json.loads(body or b"{}"))


def parse_protobuf(body: bytes) -> list[dict]:
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    req = ExportTraceServiceRequest()
    req.ParseFromString(body)
    out = []
    for rs in req.resource_spans:
        res = attrs_to_dict(MessageToDict(rs.resource).get("attributes"))
        for ss in rs.scope_spans:
            for sp in ss.spans:
                d = MessageToDict(sp)
                out.append({
                    "trace_id": sp.trace_id.hex(),
                    "span_id": sp.span_id.hex(),
                    "parent_span_id": sp.parent_span_id.hex() or None,
                    "name": sp.name,
                    "start_ns": sp.start_time_unix_nano,
                    "end_ns": sp.end_time_unix_nano,
                    "attributes": attrs_to_dict(d.get("attributes")),
                    "resource": res,
                    "status": d.get("status"),
                })
    return out


def parse_request(body: bytes, content_type: str) -> list[dict]:
    if "protobuf" in (content_type or "").lower():
        return parse_protobuf(body)
    return parse_json(body)
