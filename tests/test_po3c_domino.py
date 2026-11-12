"""PO-3c Domino adapter tests (AC PO-3.1 .. PO-3.3, PO-3.6), written from the AC, not the implementation.

Contract the builder must meet (adapters/domino.py, DominoAdapter, no auth, live via the Domino API proxy; calls go
through `requests` exactly as the existing Domino tests mock them, see tests/test_t4_intake.py)
- auth_methods() -> ["none"]  (the API proxy adds the token).
- test_connection(cfg, creds) -> [{name, detail, required, ok, fix}], at least one required item whose name or detail
  mentions "domino" (API proxy reachability). ok True when the proxy answers; ok False with a non-empty plain-language
  fix when the call raises or neither DOMINO_API_PROXY nor DOMINO_API_HOST is set. Never raises. No write flags.
- list_scopes(cfg, creds) -> tree of the CURRENT project only (env DOMINO_PROJECT_ID), selected, no typed input:
  [{id: "domino:project:<project id>", label: <project name or id>, selected: True, children: []}].
- discover(cfg, creds, scopes) -> the existing listing, for the project ids in the scope ids ("domino:project:<id>");
  platform "domino", same SourceAgent mapping as list_agents. Raises nothing for an unreachable proxy? No: like
  list_agents it propagates a RuntimeError (intake reports it).
No recordings are needed (Domino uses the in-test fake API proxy).
"""
import sys

import pytest

import _po3c_common as C

sys.path.insert(0, str(C.ROOT / "tests"))
import test_t4_intake as T4  # noqa: E402
from test_t4_intake import env, STATE, DOMINO_ID, OTHER_ID  # noqa: E402,F401

CFG = {"sample": False}


def _adapter():
    from adapters import get_adapter
    ad = get_adapter("domino")
    assert ad is not None
    return ad


@pytest.fixture
def proxy(env, monkeypatch):
    """The AC-1 fake Domino API proxy; project lookups answer {"id","name"}."""
    monkeypatch.setenv("DOMINO_API_PROXY", "http://proxy.invalid")
    orig = T4._fake_http

    def fake(url, *a, **k):
        u = str(url)
        if "proj-123" in u and "/apps" not in u:
            return T4._Resp({"id": "proj-123", "name": "fraud-lab", "ownerName": "dom-owner"})
        return orig(url, *a, **k)

    import requests
    monkeypatch.setattr(requests, "get", fake)
    monkeypatch.setattr(requests, "request", lambda m, u, *a, **k: fake(u, **k))
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, *a, **k: fake(u, **k))
    monkeypatch.setattr(requests.Session, "get", lambda self, u, *a, **k: fake(u, **k))
    return fake


def test_po31_registry_adapter_implements_all_four_methods():
    ad = _adapter()
    from adapters.base import AdapterBase
    assert isinstance(ad, AdapterBase)
    for n in ("auth_methods", "test_connection", "list_scopes", "discover"):
        assert getattr(type(ad), n) is not getattr(AdapterBase, n), n


def test_po31_auth_methods_is_none():
    assert _adapter().auth_methods() == ["none"]


def test_po31_test_connection_proxy_reachable(proxy):
    items = _adapter().test_connection(CFG, {})
    C.check_perm_shape(items)
    dom = [i for i in items if "domino" in (i["name"] + i["detail"]).lower()]
    assert dom and all(i["ok"] for i in dom) and any(i["required"] for i in dom)
    assert not C.has_write_flag(items)


def test_po31_test_connection_unreachable_gives_fix(proxy, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("proxy down")
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "request", boom)
    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(requests.Session, "get", boom)
    items = _adapter().test_connection(CFG, {})
    C.check_perm_shape(items)
    bad = [i for i in items if not i["ok"] and "domino" in (i["name"] + i["detail"]).lower()]
    assert bad and bad[0]["fix"].strip()


def test_po31_test_connection_no_proxy_configured(proxy, monkeypatch):
    for k in ("DOMINO_API_PROXY", "DOMINO_API_HOST"):
        monkeypatch.delenv(k, raising=False)
    items = _adapter().test_connection(CFG, {})
    C.check_perm_shape(items)
    assert any(not i["ok"] and "domino" in (i["name"] + i["detail"]).lower() for i in items)


def test_po32_list_scopes_current_project_selected(proxy):
    tree = _adapter().list_scopes(CFG, {})
    C.check_tree_selected(tree)
    leaves = C.leaves(tree)
    assert len(leaves) == 1 and leaves[0]["id"] == "domino:project:proj-123"
    assert leaves[0]["label"] and ("fraud-lab" in leaves[0]["label"] or "proj-123" in leaves[0]["label"])


def test_po33_discover_is_the_existing_listing(proxy):
    ad = _adapter()
    ids = [n["id"] for n in C.leaves(ad.list_scopes(CFG, {}))]
    got = ad.discover(CFG, {}, ids)
    assert [a.native_id for a in got] == [DOMINO_ID]
    from adapters.base import SourceAgent
    assert all(isinstance(a, SourceAgent) and a.platform == "domino" for a in got)
    assert [a.native_id for a in got] == [a.native_id for a in ad.list_agents()]
    assert got[0].name == "domino-deployed-agent"
    assert all(c.get("projectId") == "proj-123" for c in STATE.get("calls", []))


def test_po33_discover_follows_selected_scope(proxy):
    got = _adapter().discover(CFG, {}, ["domino:project:" + STATE["project"] + "-x"])
    assert DOMINO_ID not in [a.native_id for a in got]
    assert any(c.get("projectId") == "proj-123-x" for c in STATE.get("calls", []))


def test_po33_discover_reports_progress_when_given(proxy):
    seen = []
    _adapter().discover(CFG, {}, ["domino:project:proj-123"], progress=lambda *a: seen.append(a))
    assert seen, "progress(done, total, message) should be called at least once"


def test_po36_legacy_list_agents_unchanged(proxy):
    ags = _adapter().list_agents()
    assert [a.native_id for a in ags] == [DOMINO_ID]
