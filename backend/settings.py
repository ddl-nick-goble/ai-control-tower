"""In-app Settings (T-39): one accessor for every configurable value.

get_setting(name): the environment variable if set (a Domino Project or User variable wins), else the app value
saved from the Settings page (secrets are Fernet-encrypted in the ACT DB), else the default. Nothing is cached for
more than a couple of seconds, so a saved change is live without a restart. Secret values are never logged,
audited or returned by any API.

ACT_SECRET_KEY is read straight from os.environ on purpose: it is the key that protects the stored secrets, so it
cannot itself be an in-app setting. The recon agent (its own process) reads the same settings through get_setting
against the same ACT DB (see agents/recon_agent/config.py); the demo switch is per process (app.sh runs one worker).
"""
import base64
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import OperationalError

from . import db, demo

log = logging.getLogger(__name__)


class SettingError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# name -> (section, secret, env name, kind); LABELS/HELP below give the Settings page its text
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
    ("aws_role_arn", "credentials", False, "ACT_AWS_ROLE_ARN", "text"),
    ("aws_external_id", "credentials", False, "ACT_AWS_EXTERNAL_ID", "text"),
    ("aws_access_key_id", "credentials", True, "ACT_AWS_ACCESS_KEY_ID", "text"),
    ("aws_secret_access_key", "credentials", True, "ACT_AWS_SECRET_ACCESS_KEY", "text"),
    ("google_service_account_json", "credentials", True, "ACT_GOOGLE_SERVICE_ACCOUNT_JSON", "json"),
    ("microsoft_tenant_id", "credentials", False, "ACT_MICROSOFT_TENANT_ID", "text"),
    ("microsoft_client_id", "credentials", False, "ACT_MICROSOFT_CLIENT_ID", "text"),
    ("microsoft_client_secret", "credentials", True, "ACT_MICROSOFT_CLIENT_SECRET", "text"),
    ("databricks_host", "credentials", False, "ACT_DATABRICKS_HOST", "text"),
    ("databricks_client_id", "credentials", False, "ACT_DATABRICKS_CLIENT_ID", "text"),
    ("databricks_client_secret", "credentials", True, "ACT_DATABRICKS_CLIENT_SECRET", "text"),
    ("admins", "access", False, "ACT_ADMINS", "text"),
]
LABELS = {
    "demo_mode": ("Demo mode", "Run on a simulated database with stand-in services. Never touches real data; turn off to return to the real database."),
    "llm_provider": ("LLM provider", "Where model calls go: gateway (Domino LLM Gateway) or mock (canned answers)."),
    "llm_gateway_url": ("LLM Gateway URL", "Base URL of the Domino LLM Gateway used when the provider is gateway."),
    "break_classifier_endpoint_url": ("Break-classifier endpoint URL", "URL of the Domino model endpoint that classifies reconciliation breaks."),
    "break_classifier_access_key": ("Break-classifier access key", "Access key for the break classifier endpoint. Stored encrypted and never shown."),
    "external_allow_hosts": ("External card allowed hosts", "Comma-separated hosts external agents may be registered from."),
    "trusted_jwks_path": ("Trusted JWKS", "Path of the JWKS file used to verify external agent tokens."),
    "service_accounts": ("Service accounts", "Comma-separated service accounts allowed to call the API without a user."),
    "policy_agentic_id": ("Governance policy id (agentic bundle)", "Domino Governance policy id used for agent bundles."),
    "policy_model_id": ("Governance policy id (model bundle)", "Domino Governance policy id used for model bundles."),
    "aws_role_arn": ("AWS role ARN", "IAM role the app assumes to read AWS data."),
    "aws_external_id": ("AWS external ID", "External id sent when assuming the AWS role."),
    "aws_access_key_id": ("AWS access key ID", "Access key id for AWS. Stored encrypted and never shown."),
    "aws_secret_access_key": ("AWS secret access key", "Secret access key for AWS. Stored encrypted and never shown."),
    "google_service_account_json": ("Google service-account JSON", "Service account key (a JSON object) for Google Cloud. Stored encrypted and never shown."),
    "microsoft_tenant_id": ("Microsoft tenant ID", "Azure AD tenant for Microsoft access."),
    "microsoft_client_id": ("Microsoft client ID", "Azure AD application (client) id."),
    "microsoft_client_secret": ("Microsoft client secret", "Azure AD client secret. Stored encrypted and never shown."),
    "databricks_host": ("Databricks host", "Workspace URL of your Databricks deployment."),
    "databricks_client_id": ("Databricks client ID", "OAuth client id for Databricks."),
    "databricks_client_secret": ("Databricks client secret", "OAuth client secret for Databricks. Stored encrypted and never shown."),
    "admins": ("Admins", "Comma-separated usernames who can open Settings. The project owner is always an admin."),
}
DEFS = {n: {"name": n, "section": sec, "secret": secret, "env": env or f"ACT_{n.upper()}", "kind": kind,
            "label": LABELS[n][0], "help": LABELS[n][1]}
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


_file_cache = {"mtime": None, "path": None, "key": None}


def _write_new_key(path: Path) -> None:
    """Create the key file with mode 0600. Prefer a private temp file hard-linked into place (a reader never sees a
    half-written key and of two racing processes exactly one key wins). Where hard links are not supported
    (EPERM, ENOTSUP, EXDEV, as on some dataset mounts) create the file directly with O_EXCL; the empty-file retry
    in _file_key covers a reader that sees it before the key is written."""
    for stale in path.parent.glob(f".{path.name}.*.tmp"):  # left by a crashed process
        try:
            if time.time() - stale.stat().st_mtime > 60:
                stale.unlink()
        except OSError:
            pass
    key = Fernet.generate_key()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        try:
            os.link(tmp, path)
            return
        except FileExistsError:
            return
        except OSError:
            pass  # no hard links here: fall through to the direct create
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "wb") as f:
        f.write(key)


def _file_key() -> bytes:
    path = _key_file()
    for _ in range(50):
        try:
            mtime = path.stat().st_mtime_ns
            if _file_cache["path"] == str(path) and _file_cache["mtime"] == mtime and _file_cache["key"]:
                return _file_cache["key"]
            key = path.read_bytes().strip()
            if key:
                _file_cache.update(path=str(path), mtime=mtime, key=key)
                return key
        except FileNotFoundError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_new_key(path)
        time.sleep(0.02)
    raise RuntimeError("could not read or create the secret key file")


_fernets: dict = {}


def _fernet(material: bytes, source: str) -> Fernet:
    k = (source, material)
    if k not in _fernets:
        _fernets[k] = Fernet(material)
    return _fernets[k]


def key_state():
    """(Fernet, key_source 'env'|'file', warnings)."""
    env_key = os.environ.get("ACT_SECRET_KEY") or ""
    warnings = []
    if len(env_key) >= 32:
        return _fernet(base64.urlsafe_b64encode(hashlib.sha256(env_key.encode()).digest()), "env"), "env", warnings
    if env_key:
        warnings.append("ACT_SECRET_KEY is too short (it needs 32 or more characters), so it is ignored and a "
                        "generated key file is used instead. Set a longer Project secret variable ACT_SECRET_KEY.")
    elif not demo.enabled():
        warnings.append("Secrets are encrypted with a key file generated beside the database. Set the Project "
                        "secret variable ACT_SECRET_KEY (32 or more characters) so the key is kept apart from the data. "
                        "Changing the key later makes stored secrets unreadable, and they must be entered again.")
    return _fernet(_file_key(), "file"), "file", warnings


# ---- storage ----
def _rows() -> dict:
    with _lock:
        if time.monotonic() - _cache["at"] < _TTL:
            return _cache["rows"]
        previous = _cache["rows"]
    if not Path(db.REAL_DB_PATH).expanduser().exists():
        rows = {}  # nothing saved yet; connecting would make SQLite create an empty database file (a stray act.db)
        with _lock:
            _cache.update(at=time.monotonic(), rows=rows)
        return rows
    try:
        with db.real_engine.connect() as cx:
            rows = {r.name: r for r in cx.execute(db.app_settings.select()).all()}
    except OperationalError as e:
        if "no such table" in str(e).lower():  # not created yet: nothing is saved
            rows = {}
        else:  # a transient lock or I/O error: keep what we knew instead of reporting every secret as unset
            log.warning("settings could not be read (%s); using the last known values", type(e).__name__)
            rows = previous
    except Exception:  # noqa: BLE001
        log.warning("settings could not be read; using the last known values", exc_info=True)
        rows = previous
    with _lock:
        _cache.update(at=time.monotonic(), rows=rows)
    return rows


def _invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0


def _env_value(name):
    env = DEFS[name]["env"]
    if env in demo.applied_env():  # demo mode filled this in itself: not a project variable, so not locked, and the
        # banner must not claim it is (the value shows as a default that Settings can override)
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
_warned_no_admin = False


def _admin_names() -> set:
    raw = (get_setting("admins") or "").replace(";", ",").replace("\n", ",")
    return {a.strip() for a in raw.split(",")} - {""}


def settings_open() -> bool:
    """SET-1: Settings are open to anyone who can reach the app unless ACT_SETTINGS_OPEN=0 (single source)."""
    return (os.environ.get("ACT_SETTINGS_OPEN") or "").strip() != "0"


def admins_configured() -> bool:
    return bool((os.environ.get("DOMINO_PROJECT_OWNER") or "").strip()) or bool(_admin_names())


def is_admin(user) -> bool:
    global _warned_no_admin
    owner = (os.environ.get("DOMINO_PROJECT_OWNER") or "").strip()
    if owner and user.username == owner:
        return True
    if not admins_configured() and not _warned_no_admin:
        _warned_no_admin = True
        log.warning("Settings: DOMINO_PROJECT_OWNER is not set and no admins are listed, so nobody can open Settings. "
                    "Set ACT_ADMINS (a project variable) to a comma-separated list of usernames.")
    return user.username in _admin_names()


# ---- API model ----
def entry(name: str) -> dict:
    d = DEFS[name]
    env_v = _env_value(name)
    row = _rows().get(name)
    app_set = row is not None and row.value is not None
    source = "env" if env_v is not None else "app" if app_set else "default"
    out = {"name": name, "section": d["section"], "label": d["label"], "help": d["help"], "secret": d["secret"], "env": d["env"], "is_set": source != "default",
           "source": source, "locked": source == "env",
           "changed_by": row.changed_by if source == "app" else None,
           "changed_at": row.changed_at if source == "app" else None}
    if d["secret"]:
        out["decryptable"] = True
        if source == "app":
            try:
                key_state()[0].decrypt(row.value.encode())
            except (InvalidToken, ValueError):  # ACT_SECRET_KEY changed after this was saved: enter it again
                out["decryptable"] = False
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


def _source(name, row) -> str:
    return "app" if row is not None and row.value is not None else "none"


def _check_writable(name):
    if name not in DEFS:
        raise SettingError(404, "unknown setting")
    if _env_value(name) is not None:
        raise SettingError(423, "set by a project variable; change it there")


def _store(user, name, stored, action):
    """Write the value and its audit event in one transaction (the audit never carries the value)."""
    with db.real_engine.begin() as cx:
        before = cx.execute(db.app_settings.select().where(db.app_settings.c.name == name)).first()
        cx.execute(db.app_settings.delete().where(db.app_settings.c.name == name))
        if stored is not None:
            cx.execute(db.app_settings.insert().values(name=name, value=stored, is_secret=DEFS[name]["secret"],
                                                       changed_by=user.username, changed_at=now()))
        cx.execute(db.audit_events.insert().values(
            actor_user_id=user.user_id, actor_username=user.username, action=action, target=name, ts=now(),
            detail={"name": name, "secret": DEFS[name]["secret"],
                    "previous_source": _source(name, before), "new_source": "app" if stored is not None else "cleared"}))
    _invalidate()


def _switch_off_or_409() -> None:
    try:
        demo.switch_off(own=1)
    except demo.DemoGuardError as e:
        raise SettingError(409, f"{e}; nothing was changed, try again")


def _restore_demo_on() -> None:
    """The switch-off happened but the value could not be saved: put the live mode back to match what is stored.
    A failure here is a 503 (the state is uncertain), never a 500."""
    try:
        demo.switch_on(own=1)
    except Exception:  # noqa: BLE001
        log.exception("could not restore demo mode after a failed save")
        raise SettingError(503, "the setting could not be saved and demo mode could not be restored; try again")


def _switch_off_retry() -> None:
    """Rollback of a switch-on whose value was not saved: retry a few times (a drain timeout is transient). The
    whole retry stays inside the 30 s request gate: each attempt waits up to demo.DRAIN_TIMEOUT."""
    deadline = time.monotonic() + 25.0
    for _ in range(3):
        try:
            demo.switch_off(own=1)
            return
        except demo.DemoGuardError:
            if time.monotonic() + demo.DRAIN_TIMEOUT > deadline:
                break
            time.sleep(0.05)
    raise SettingError(503, "the value was not saved and demo mode could not be switched back off yet; try again")


# Serialises was_on + switch + _store for demo_mode across requests (else two overlapping toggles can leave the
# stored value and the live mode disagreeing). Taken before demo._Switch steps out, never while holding _rt_lock.
_demo_setting_lock = threading.Lock()


@contextmanager
def _demo_locked():
    demo.acquire_outside_flight(_demo_setting_lock, own=1)
    try:
        yield
    finally:
        _demo_setting_lock.release()


def _put_demo(user, name, v) -> None:
    with _demo_locked():
        was_on = demo.enabled()
        if v == "true":
            try:
                demo.switch_on(own=1)  # atomic: on failure nothing has changed
            except demo.DemoGuardError as e:
                raise SettingError(409, str(e))
            try:
                _store(user, name, v, "settings.update")
            except Exception:
                log.exception("demo_mode could not be saved")
                if not was_on:
                    _switch_off_retry()  # the value was not saved, so the app must not stay in demo mode
                    raise SettingError(503, "the setting could not be saved; demo mode was left off, try again")
                raise SettingError(503, "the setting could not be saved; demo mode is still on, try again")
        else:
            _switch_off_or_409()  # first: if it cannot happen, nothing is stored and the two stay consistent
            _store_or_restore(user, name, v, "settings.update", was_on)


def _store_or_restore(user, name, stored, action, was_on) -> None:
    try:
        _store(user, name, stored, action)
    except Exception:
        log.exception("demo_mode could not be saved")
        if was_on:
            _restore_demo_on()
        raise SettingError(503, "the setting could not be saved; demo mode was left as it was, try again")


def put_setting(user, name, value) -> dict:
    _check_writable(name)
    v = _validate(name, value)
    if name == "demo_mode":
        _put_demo(user, name, v)
        return entry(name)
    stored = key_state()[0].encrypt(v.encode()).decode() if DEFS[name]["secret"] else v
    _store(user, name, stored, "settings.update")
    return entry(name)


def delete_setting(user, name) -> dict:
    _check_writable(name)
    if name == "demo_mode":
        with _demo_locked():
            was_on = demo.enabled()
            _switch_off_or_409()
            _store_or_restore(user, name, None, "settings.clear", was_on)
        return entry(name)
    _store(user, name, None, "settings.clear")
    return entry(name)


def apply_demo_setting() -> None:
    """At startup: restore the demo switch saved from the Settings page (ACT_DEMO_MODE in the env wins)."""
    if demo.env_enabled() or _env_value("demo_mode") is not None:
        return
    row = _rows().get("demo_mode")
    if row is not None and row.value == "true":
        try:
            demo.switch_on()
        except demo.DemoGuardError:
            raise
        except Exception:  # noqa: BLE001 - a failed seed must not stop the app from starting on the real data
            log.exception("could not restore the saved demo mode; starting on the real database")
            raise demo.DemoGuardError("demo mode could not be restored: see the server log")
