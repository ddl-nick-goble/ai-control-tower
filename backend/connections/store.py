"""Connection storage and the state machine.

States: draft -> waiting -> trusted -> verified | partial -> connected, plus needs_attention | failed, and
disconnected (DELETE keeps the row and its agents). Always read db.engine at call time so the demo-mode DB switch works.
"""
import copy
import re
import os
import secrets
import time

from sqlalchemy import func, insert, select, update

from .. import db
from ..normalize import now_iso
from . import PROVIDERS, sample

STATES = ("draft", "waiting", "trusted", "verified", "partial", "connected", "needs_attention", "failed",
          "disconnected")
TRUSTED_OR_LATER = ("trusted", "verified", "partial", "connected", "needs_attention")
now = time.time  # injectable clock (epoch seconds)


class ConnectionError_(Exception):
    """A user-facing problem; `status` is the HTTP code the route should use."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.message, self.status = message, status


def trust_delay() -> float:
    try:
        return float(os.environ.get("ACT_SAMPLE_TRUST_DELAY_S", "3"))
    except ValueError:
        return 3.0


def audit(cx, actor, action, target, detail=None):
    cx.execute(insert(db.audit_events).values(
        actor_user_id=actor.user_id, actor_username=actor.username, action=action, target=target, ts=now_iso(),
        detail=detail or {}))


def _row(cx, cid):
    r = cx.execute(select(db.connections).where(db.connections.c.id == cid)).first()
    return None if r is None else r._mapping


def _by_token(cx, token):
    r = cx.execute(select(db.connections).where(db.connections.c.state_token == token)).first()
    return None if r is None else r._mapping


def _set(cx, cid, **vals):
    cx.execute(update(db.connections).where(db.connections.c.id == cid).values(updated_at=now_iso(), **vals))


def create(provider: str, sample_mode: bool, actor):
    """-> (row dict, state_token). Sample connections start waiting straight away (nothing to open)."""
    if provider not in PROVIDERS:
        raise ConnectionError_(f"Unknown provider. Choose one of: {', '.join(PROVIDERS)}.", 400)
    cid, token = "conn-" + secrets.token_hex(6), secrets.token_urlsafe(24)
    with db.engine.begin() as cx:
        cx.execute(insert(db.connections).values(
            id=cid, provider=provider, state="waiting" if sample_mode else "draft", sample=sample_mode,
            state_token=token, token_used=False, config={}, scopes=None, created_by=actor.user_id,
            created_by_username=actor.username, created_at=now_iso(), updated_at=now_iso(),
            waiting_since=now() if sample_mode else None))
        audit(cx, actor, "connection_connect", cid, {"provider": provider, "sample": sample_mode})
        return dict(_row(cx, cid)), token


def _tree_ids(nodes):
    for n in nodes:
        yield n["id"]
        yield from _tree_ids(n.get("children") or [])


def scope_tree(row) -> list:
    """The scope tree with `selected` filled in (everything by default). Sample tenants come from fixtures; live
    trees are the one cached in the connection's scopes JSON by refresh_tree() (never computed here, so GET
    list/view stay cheap and offline)."""
    scopes = row["scopes"] or {}
    tree = sample.tree(row["provider"]) if row["sample"] else copy.deepcopy(scopes.get("tree") or [])
    chosen = scopes.get("selected")

    def mark(nodes):
        for n in nodes:
            n["selected"] = chosen is None or n["id"] in chosen
            n["children"] = mark(n.get("children") or [])
        return nodes
    return mark(tree)


NOT_LIVE = {"name": "Live account", "detail": "", "required": True, "ok": False,
            "fix": "ACT is not connected to a live account yet. Finish the setup step so ACT can sign in, "
                   "or use a sample connection to walk the flow now."}


def _live_context(row):
    """-> (adapter, creds, problem). `problem` is a checklist item when the adapter or credentials are missing."""
    from adapters import get_adapter
    from . import federation
    adapter = get_adapter(row["provider"])
    if adapter is None:
        return None, None, {"name": "Provider adapter", "detail": "", "required": True, "ok": False,
                            "fix": f"Live access checks for {row['provider']} arrive with its adapter. "
                                   "Use a sample connection to walk the flow now."}
    cfg = dict(row["config"] or {})
    if row["provider"] == "aws" and not cfg.get("role_arn"):
        cfg["role_arn"] = cfg.get("aws_role_arn") or cfg.get("role")
    needed = {"aws": "role_arn", "google": "audience", "microsoft": "tenant_id", "databricks": "host",
              "snowflake": "account"}[row["provider"]]
    if row["provider"] == "google" and not cfg.get("audience") and cfg.get("gcp_wif_provider"):
        cfg["audience"] = "//iam.googleapis.com/" + cfg["gcp_wif_provider"].lstrip("/")
    if not cfg.get(needed):
        return adapter, None, NOT_LIVE
    try:
        return adapter, federation.exchange(row["provider"], cfg), None
    except Exception as e:  # noqa: BLE001 - shown to the user as a fix, never a traceback
        return adapter, None, {"name": "Sign in to the provider", "detail": str(e)[:200], "required": True,
                               "ok": False, "fix": "ACT could not get a credential from the provider. "
                               "Check the trust setup and try again."}


def refresh_tree(cx, row, ctx=None):
    """Compute the live scope tree through the adapter and cache it in scopes JSON. Called from /scopes and /check
    only. Returns the fresh row."""
    if row["sample"]:
        return row
    adapter, creds, problem = ctx or _live_context(row)
    if problem is None:
        try:
            tree = adapter.list_scopes(row["config"] or {}, creds)
        except NotImplementedError:
            return row
        _set(cx, row["id"], scopes={**(row["scopes"] or {}), "tree": tree})
        return _row(cx, row["id"])
    return row


def _found(row) -> dict:
    """What the handshake found, for the Detected strip: {role, account} (AWS), from the connection's config (the
    paste or callback value) or, for a sample connection, the sample tenant's identity."""
    if row["provider"] != "aws":
        return {}
    cfg = row["config"] or {}
    arn = cfg.get("role_arn") or cfg.get("aws_role_arn") or cfg.get("role") or ""
    if not arn and row["sample"]:
        arn = sample.tenant("aws").get("identity", "")
    m = re.match(r"arn:aws[a-z-]*:iam::(\d{12}):role/(?:.*/)?([^/]+)$", arn)
    role, account = (m.group(2), m.group(1)) if m else (arn or None, None)
    account = cfg.get("account_id") or account
    return {k: v for k, v in (("role", role), ("account", account)) if v}


def view(cx, row) -> dict:
    """The public shape of a connection."""
    tree = scope_tree(row)
    ids = list(_tree_ids(tree))
    selected = [n for n in ids if _selected(tree, n)]
    agents = cx.execute(select(func.count()).select_from(db.agents).where(
        db.agents.c.connection_id == row["id"])).scalar() or 0
    return {"id": row["id"], "provider": row["provider"], "state": row["state"], "sample": bool(row["sample"]),
            "scopes": {"selected": selected, "total": len(ids)}, "last_check": row["last_check"],
            "last_scan": row["last_scan"], "agent_count": agents, "config": row["config"] or {},
            "found": _found(row),
            "created_by": row["created_by_username"], "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def _selected(tree, node_id):
    for n in tree:
        if n["id"] == node_id:
            return n["selected"]
        r = _selected(n["children"], node_id)
        if r is not None:
            return r
    return None


def poll(cx, cid):
    """Called on every GET: draft -> waiting, and a sample connection trusts itself after the delay."""
    row = _row(cx, cid)
    if row is None:
        return None
    if row["state"] == "draft":
        _set(cx, cid, state="waiting", waiting_since=now())
        row = _row(cx, cid)
    if row["state"] == "waiting" and row["sample"]:
        since = row["waiting_since"] or now()
        if now() - since >= trust_delay():
            _set(cx, cid, state="trusted", token_used=True)
            row = _row(cx, cid)
    return row


def get(cid):
    with db.engine.begin() as cx:
        row = poll(cx, cid)
        return None if row is None else view(cx, row)


def list_all():
    with db.engine.begin() as cx:
        rows = cx.execute(select(db.connections).order_by(db.connections.c.created_at.desc())).all()
        out = []
        for r in rows:
            r = poll(cx, r.id)
            out.append(view(cx, r))
        return out


def use_callback(token: str, extra: dict):
    """-> (ok, message, row|None). A plain-language answer for every failure."""
    with db.engine.begin() as cx:
        row = _by_token(cx, token) if token else None
        if row is None:
            return False, ("ACT doesn't recognise that setup link. Go back to ACT, start the connection again "
                           "and use the new link."), None
        if row["state"] in TRUSTED_OR_LATER:
            return True, (f"This setup link was already used and {row['provider']} is already trusted, so there is "
                          "nothing more to do. You can close this tab."), dict(row)
        if row["token_used"] or row["state"] not in ("draft", "waiting"):
            return False, ("This setup link was already used. Go back to ACT; "
                           "if the connection isn't ready, start it again for a fresh link."), None
        cfg = {**(row["config"] or {}), **{k: v for k, v in extra.items() if k != "state"}}
        _set(cx, row["id"], state="trusted", token_used=True, config=cfg)
        return True, f"Connected to {row['provider']}. You can close this tab; ACT continues on its own.", \
            dict(_row(cx, row["id"]))


def template_for_state(token: str):
    with db.engine.begin() as cx:
        row = _by_token(cx, token)
        return dict(row) if row else None


def apply_paste(cid, result: dict, actor):
    """Record a classified paste; a recognised value trusts the connection."""
    with db.engine.begin() as cx:
        row = _row(cx, cid)
        if row is None:
            return None
        if row["state"] == "disconnected":
            raise ConnectionError_("This connection was disconnected. Start a new one.")
        vals = {}
        if result["recognised"]:
            vals["config"] = {**(row["config"] or {}), result["kind"]: result["value"]}
            if result.get("account_id"):
                vals["config"]["account_id"] = result["account_id"]
            if row["state"] in ("draft", "waiting"):
                vals.update(state="trusted", token_used=True)
        if vals:
            _set(cx, cid, **vals)
        audit(cx, actor, "connection_paste", cid, {"kind": result["kind"], "recognised": result["recognised"]})
        return view(cx, _row(cx, cid))


def run_check(cid, actor):
    """-> {state, permissions}. Sample tenants answer from fixtures (green on the second check); live ones use the
    provider adapter's test_connection when it exists."""
    with db.engine.begin() as cx:
        row = _row(cx, cid)
        if row is None:
            return None
        if row["state"] not in TRUSTED_OR_LATER:
            raise ConnectionError_("ACT hasn't seen the trust yet. Finish the setup step first, then check access.")
        if row["sample"]:
            before = cx.execute(select(func.count()).select_from(db.connection_checks).where(
                db.connection_checks.c.connection_id == cid)).scalar() or 0
            perms = sample.permissions(row["provider"], recheck=before > 0)
        else:
            perms = _live_check(cx, row)
        required_missing = any(not p["ok"] and p.get("required") for p in perms)
        state = "needs_attention" if required_missing and not row["sample"] else \
            "verified" if all(p["ok"] for p in perms) else "partial"
        ts = now_iso()
        cx.execute(insert(db.connection_checks).values(connection_id=cid, ts=ts, state=state, permissions=perms))
        if row["state"] != "connected":
            _set(cx, cid, state=state, last_check=ts)
        else:
            _set(cx, cid, last_check=ts)
        return {"state": state if row["state"] != "connected" else "connected", "permissions": perms,
                "last_check": ts}


def _live_check(cx, row) -> list:
    adapter, creds, problem = _live_context(row)
    if problem is not None:
        return [problem]
    try:
        items = adapter.test_connection(row["config"] or {}, creds)
    except NotImplementedError:
        items = [{"name": "Provider adapter", "ok": False, "required": True,
                  "fix": f"Live access checks for {row['provider']} arrive with its adapter."}]
    refresh_tree(cx, row, (adapter, creds, None))
    return [{"name": p.get("name"), "detail": p.get("detail", ""), "required": bool(p.get("required")),
             "ok": bool(p.get("ok")), "fix": p.get("fix")} for p in items]


def save_scopes(cid, selected: list):
    with db.engine.begin() as cx:
        row = _row(cx, cid)
        if row is None:
            return None
        valid = set(_tree_ids(scope_tree(row)))
        unknown = [s for s in selected if s not in valid]
        if unknown:
            raise ConnectionError_(f"Unknown scope: {unknown[0]}", 400)
        _set(cx, cid, scopes={**(row["scopes"] or {}), "selected": list(dict.fromkeys(selected))})
        return scope_tree(_row(cx, cid))


def disconnect(cid, actor):
    with db.engine.begin() as cx:
        row = _row(cx, cid)
        if row is None:
            return None
        _set(cx, cid, state="disconnected", disconnected_at=now_iso())
        audit(cx, actor, "connection_disconnect", cid,
              {"provider": row["provider"], "agents_kept": True})
        return view(cx, _row(cx, cid))


def tree_for(cid):
    """/scopes: refreshes a live connection's cached tree first."""
    with db.engine.begin() as cx:
        row = _row(cx, cid)
        if row is None:
            return None
        if not row["sample"] and row["state"] in TRUSTED_OR_LATER:
            row = refresh_tree(cx, row)
        return scope_tree(row)


def provider_of(cid):
    with db.engine.begin() as cx:
        row = _row(cx, cid)
        return None if row is None else row["provider"]
