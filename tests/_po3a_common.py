"""Shared helpers for the PO-3a provider adapter tests (aws + snowflake).

RECORDED HTTP CONTRACT (builder: create these files, shaped like the real API responses from the research docs).
Each recording is a JSON file  fixtures/http/<provider>/<name>.json  =  {"status": int, "headers": {..}, "json": <body>}
which is exactly what an adapter module's `_http(method, url, **kw) -> (status, headers, json)` returns.
Both adapter modules (adapters/aws.py, adapters/snowflake.py) must route EVERY network call through a module-level
`_http(method, url, **kw)` (the tests monkeypatch it). Sample mode (cfg["sample"] true) must never call it.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"

PLACEHOLDERS = {"tbd", "todo", "n/a", "na", "none", "null", "placeholder", "lorem ipsum", "string", "changeme",
                "dummy", "fake", "example", "xxx", "?", "-"}


def rec(provider, name):
    p = FIX / "http" / provider / f"{name}.json"
    if not p.exists():
        pytest.fail(f"missing recording fixtures/http/{provider}/{name}.json")
    d = json.loads(p.read_text())
    assert {"status", "json"} <= set(d), f"{p.name} must hold status/headers/json"
    return d


def resp(r):
    return r["status"], dict(r.get("headers") or {}), r["json"]


def not_found():
    return 404, {}, {"__type": "ResourceNotFoundException", "message": "not found"}


def walk(o):
    """Yield every (key, value) pair in a nested structure."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def names_of(items):
    return {(t.get("name") if isinstance(t, dict) else t) for t in items}


def assert_no_invented(agent):
    """Unknowns are the string "unknown" or None/empty; never a made-up placeholder."""
    d = dict(vars(agent))
    d.pop("raw", None)
    for s in strings(json.loads(json.dumps(d, default=str))):
        assert s.strip().lower() not in PLACEHOLDERS, (agent.name, s)
    assert agent.owner is None or (isinstance(agent.owner, str) and agent.owner.strip())


def block_network(monkeypatch, mod):
    """Sample mode: any HTTP at all is a failure."""
    def boom(*a, **k):
        raise AssertionError("network used in sample mode")
    monkeypatch.setattr(mod, "_http", boom, raising=True)
    import requests
    import httpx
    for obj, attr in ((requests, "get"), (requests, "post"), (requests, "request"),
                      (requests.Session, "request"), (httpx, "get"), (httpx, "post"), (httpx.Client, "request")):
        monkeypatch.setattr(obj, attr, boom)


def check_perm_shape(items):
    assert isinstance(items, list) and items
    for it in items:
        assert {"name", "detail", "required", "ok", "fix"} <= set(it), it
        assert isinstance(it["ok"], bool) and isinstance(it["required"], bool)
        if not it["ok"]:
            assert isinstance(it["fix"], str) and it["fix"].strip(), it


def flatten(tree):
    for n in tree:
        yield n
        yield from flatten(n.get("children") or [])


def leaves(tree):
    return [n for n in flatten(tree) if not n.get("children")]


def check_tree_selected(tree):
    assert isinstance(tree, list) and tree
    for n in flatten(tree):
        assert {"id", "label", "selected", "children"} <= set(n), n
        assert n["selected"] is True, n


def has_write_flag(items):
    return [it for it in items
            if "read-only" in json.dumps(it).lower() and "can also" in json.dumps(it).lower()]


def sample_cfg_agents(provider):
    d = json.loads((FIX / "sample_tenants" / f"{provider}.json").read_text())
    return d.get("agents")


def action_names(text):
    return re.findall(r"[a-z][a-z0-9-]+:[A-Za-z*]+", text or "")
