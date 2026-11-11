"""In-app Settings (T-39): one accessor for every configurable value.

get_setting(name): the environment variable if set (a Domino Project or User variable wins), else the app value
saved from the Settings page (secrets are Fernet-encrypted in the ACT DB), else the default. Nothing is cached for
more than a couple of seconds, so a saved change is live without a restart. Secret values are never logged,
audited or returned by any API.
"""
import base64
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from . import db, demo

log = logging.getLogger(__name__)


class SettingError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# name -> (section, secret, env name, kind, label)
_DEFS = [
    ("demo_mode", "demo", False, "ACT_DEMO_MODE", "bool"),
    ("llm_provider", "integrations", False, "ACT_LLM_PROVIDER", "provider"),
    ("llm_gateway_url", "integrations", False, "LLM_GATEWAY_URL", "url"),
    ("break_classifier_endpoint_url", "integrations", False, "BREAK_CLASSIFIER_ENDPOINT_URL", "url"),
    ("break_classifier_access_key", "integrations", True, "BREAK_CLASSIFIER_ACCESS_KEY", "text"),
    ("external_allow_hosts", "integrations", False, "ACT_EXTERNAL_ALLOW_HOSTS", "text"),
    ("trusted_jwks_path", "integrations", False, "ACT_TRUSTED_JWKS_PATH", "text"),
    ("service_accounts", "integrations", False, "ACT_SERVICE_ACCOUNTS", "text"),
    ("policy_agentic_id", "integrations", False, "ACT_POLICY_AGENTIC_ID", "text"),
    ("policy_model_id", "integrations", False, "ACT_POLICY_MODEL_ID", "text"),
    ("aws_role_arn", "credentials", False, None, "text"),
    ("aws_external_id", "credentials", False, None, "text"),
    ("aws_access_key_id", "credentials", True, None, "text"),
    ("aws_secret_access_key", "credentials", True, None, "text"),
    ("google_service_account_json", "credentials", True, None, "json"),
    ("microsoft_tenant_id", "credentials", False, None, "text"),
    ("microsoft_client_id", "credentials", False, None, "text"),
    ("microsoft_client_secret", "credentials", True, None, "text"),
    ("databricks_host", "credentials", False, None, "text"),
    ("databricks_client_id", "credentials", False, None, "text"),
    ("databricks_client_secret", "credentials", True, None, "text"),
    ("admins", "access", False, "ACT_ADMINS", "text"),
]
DEFS = {n: {"name": n, "section": sec, "secret": secret, "env": env or f"ACT_{n.upper()}", "kind": kind}
        for n, sec, secret, env, kind in _DEFS}
# Used while demo mode is on and neither the environment nor the app sets the value.
_DEMO_DEFAULTS = {"llm_provider": "mock", "external_allow_hosts": "127.0.0.1,localhost"}
_TRUE = ("1", "true", "yes", "on")
_TTL = 2.0
_cache = {"at": 0.0, "rows": {}}
_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- key handling ----
def _key_file() -> Path:
    return Path(db.REAL_DB_PATH).expanduser().resolve().parent / "act-secret.key"


def _file_key() -> bytes:
    path = _key_file()
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another process won the race
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


def key_state():
    """(Fernet, key_source 'env'|'file', warnings)."""
    env_key = os.environ.get("ACT_SECRET_KEY") or ""
    warnings = []
    if len(env_key) >= 32:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(env_key.encode()).digest())), "env", warnings
    if env_key:
        warnings.append("ACT_SECRET_KEY is too short (it needs 32 or more characters), so it is ignored and a "
                        "generated key file is used instead. Set a longer Project secret variable ACT_SECRET_KEY.")
    elif not demo.enabled():
        warnings.append("Secrets are encrypted with a key file generated beside the database. Set the Project "
                        "secret variable ACT_SECRET_KEY (32 or more characters) so the key is kept apart from the data.")
    return Fernet(_file_key()), "file", warnings


# ---- storage ----
def _rows() -> dict:
    with _lock:
        if time.monotonic() - _cache["at"] < _TTL:
            return _cache["rows"]
    try:
        with db.real_engine.connect() as cx:
            rows = {r.name: r for r in cx.execute(db.app_settings.select()).all()}
    except Exception:  # noqa: BLE001  table not created yet
        rows = {}
    with _lock:
        _cache.update(at=time.monotonic(), rows=rows)
    return rows


def _invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0


def _env_value(name):
    env = DEFS[name]["env"]
    if env in demo.applied_env():
        return None
    v = os.environ.get(env)
    return v if v is not None and v.strip() else None


def get_setting(name: str, default=None):
    d = DEFS[name]
    v = _env_value(name)
    if v is not None:
        return v
    row = _rows().get(name)
    if row is not None and row.value is not None:
        if d["secret"]:
            try:
                return key_state()[0].decrypt(row.value.encode()).decode()
            except (InvalidToken, ValueError):
                log.error("setting %s could not be decrypted (the secret key changed?)", name)
                return default
        return row.value
    if name in _DEMO_DEFAULTS and demo.enabled():
        return _DEMO_DEFAULTS[name]
    return default


# ---- admins ----
def is_admin(user) -> bool:
    owner = (os.environ.get("DOMINO_PROJECT_OWNER") or "").strip()
    if owner and user.username == owner:
        return True
    admins = {a.strip() for a in (get_setting("admins") or "").replace(";", ",").replace("\n", ",").split(",")}
    return user.username in admins - {""}


# ---- API model ----
def entry(name: str) -> dict:
    d = DEFS[name]
    env_v = _env_value(name)
    row = _rows().get(name)
    app_set = row is not None and row.value is not None
    source = "env" if env_v is not None else "app" if app_set else "default"
    out = {"name": name, "section": d["section"], "secret": d["secret"], "is_set": source != "default",
           "source": source, "locked": source == "env",
           "changed_by": row.changed_by if source == "app" else None,
           "changed_at": row.changed_at if source == "app" else None}
    if not d["secret"]:
        if d["kind"] == "bool":
            raw = env_v if env_v is not None else (row.value if app_set else "")
            out["value"] = str(raw).strip().lower() in _TRUE
        else:
            v = get_setting(name)
            out["value"] = v if v is not None else ""
    return out


def snapshot() -> dict:
    _, source, warnings = key_state()
    return {"settings": [entry(n) for n in DEFS], "key_source": source, "warnings": warnings}


def _validate(name: str, value):
    d = DEFS[name]
    kind = d["kind"]
    if kind == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.strip().lower() in _TRUE + ("0", "false", "no", "off"):
            return "true" if value.strip().lower() in _TRUE else "false"
        raise SettingError(422, "value must be true or false")
    if not isinstance(value, str) or not value.strip():
        raise SettingError(422, "value must be a non-empty string (use clear to remove a setting)")
    value = value.strip() if not d["secret"] else value
    if len(value) > 20000:
        raise SettingError(422, "value is too long")
    if kind == "provider" and value.lower() not in ("gateway", "mock"):
        raise SettingError(422, "LLM provider must be gateway or mock")
    if kind == "provider":
        value = value.lower()
    if kind == "url":
        u = urlparse(value)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise SettingError(422, "value must be an http or https URL")
    if kind == "json":
        try:
            ok = isinstance(json.loads(value), dict)
        except ValueError:
            ok = False
        if not ok:
            raise SettingError(422, "value must be a JSON object")
    return value


def _audit(user, action, name) -> None:
    with db.real_engine.begin() as cx:
        cx.execute(db.audit_events.insert().values(
            actor_user_id=user.user_id, actor_username=user.username, action=action, target=name, ts=now(),
            detail={"name": name, "secret": DEFS[name]["secret"]}))


def _check_writable(name):
    if name not in DEFS:
        raise SettingError(404, "unknown setting")
    if _env_value(name) is not None:
        raise SettingError(423, "set by a project variable; change it there")


def _store(user, name, stored, action):
    with db.real_engine.begin() as cx:
        cx.execute(db.app_settings.delete().where(db.app_settings.c.name == name))
        if stored is not None:
            cx.execute(db.app_settings.insert().values(name=name, value=stored, is_secret=DEFS[name]["secret"],
                                                       changed_by=user.username, changed_at=now()))
    _audit(user, action, name)
    _invalidate()


def put_setting(user, name, value) -> dict:
    _check_writable(name)
    v = _validate(name, value)
    if name == "demo_mode":
        if v == "true":
            try:
                demo.switch_on()
            except demo.DemoGuardError as e:
                raise SettingError(409, str(e))
        _store(user, name, v, "settings.update")
        if v == "false":
            demo.switch_off()
        return entry(name)
    stored = key_state()[0].encrypt(v.encode()).decode() if DEFS[name]["secret"] else v
    _store(user, name, stored, "settings.update")
    return entry(name)


def delete_setting(user, name) -> dict:
    _check_writable(name)
    _store(user, name, None, "settings.clear")
    if name == "demo_mode":
        demo.switch_off()
    return entry(name)


def apply_demo_setting() -> None:
    """At startup: restore the demo switch saved from the Settings page (ACT_DEMO_MODE in the env wins)."""
    if demo.env_enabled() or _env_value("demo_mode") is not None:
        return
    row = _rows().get("demo_mode")
    if row is not None and row.value == "true":
        demo.switch_on()
