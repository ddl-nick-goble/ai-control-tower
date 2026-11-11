"""External (client-side) A2A agent intake: fetch an Agent Card, verify its JWS, build the record and passport.

Evidence, not enforcement: an invalid signature is recorded on the record and in the audit event; the
record is still created (pending_review) so a reviewer can see it.

Tier rule: the seven consequence criteria go through tiering.score_tier (backtest/benchmark keys are
rejected by no_benchmark_rule). Then the money_movement_floor applies: if any skill id, name or tag
contains payments/transfer/payment/wire/funds, the card can move money, so the tier is never
lower-consequence than Tier 2 (tier = min(tier, 2); Tier 1 is highest consequence).

JWS: signatures[] entries are verified over  protected_b64 + "." + b64url(compact key-sorted JSON of the
card without "signatures")  with trusted keys from ACT_TRUSTED_JWKS_PATH (a JWK or {"keys": [...]}),
matched by kid. ES256 and RS256 only. Library: cryptography (Apache-2.0/BSD).

SSRF: the host is resolved inside the 5 s deadline, every address is vetted (anything not globally routable is
refused unless allowlisted in ACT_EXTERNAL_ALLOW_HOSTS; port 8899 and link-local are always
refused) and the connection is made to the vetted IP (Host header and SNI keep the original name).
"""
import base64
import copy
import hashlib
import http.client
import ipaddress
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .settings import get_setting

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 5.0
MAX_BYTES = 1_000_000
MONEY_WORDS = ("payments", "transfer", "payment", "wire", "funds")
EXPIRY_DAYS = {1: 90, 2: 180, 3: 365, 4: 365}


class ExternalError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def external_id(card_url: str) -> str:
    return f"act:external:{hashlib.sha256(card_url.encode()).hexdigest()[:16]}"


def check_url(url) -> str:
    if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        raise ExternalError(422, "card_url must be an http or https URL")
    return url


def _allow_hosts() -> set:
    return {h.strip().lower().strip("[]") for h in (get_setting("external_allow_hosts") or "").split(",") if h.strip()}


def _always_blocked(ip) -> bool:
    if ip.version == 6 and ip in _COMPAT96:
        return True  # deprecated IPv4-compatible ::/96 (includes ::, ::1)
    return ip.is_link_local  # 169.254.0.0/16, fe80::/10


def _ip_blocked(ip) -> bool:
    return not ip.is_global


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_COMPAT96 = ipaddress.ip_network("::/96")  # deprecated IPv4-compatible


def _unwrap(ip):
    """Return the IPv4 embedded in IPv4-mapped, NAT64 (64:ff9b::/96) and 6to4 (2002::/16) addresses."""
    if ip.version == 6:
        if ip.ipv4_mapped:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.sixtofour:
            return ip.sixtofour
    return ip


def resolve_checked(host: str, port: int, deadline: float) -> list:
    """Resolve host inside the deadline and return the vetted addresses to connect to (pinned)."""
    bad = ExternalError(422, "card_url address is not allowed (internal, loopback, link-local or reserved)")
    if port == 8899:
        raise bad
    result = {}

    def work():
        try:
            result["v"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception as e:  # noqa: BLE001
            result["e"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(max(deadline - time.monotonic(), 0))
    if t.is_alive():
        raise ExternalError(504, "timed out resolving the Agent Card host (5 s)")
    if "e" in result:
        raise ExternalError(502, "could not resolve the Agent Card host")
    ips = []
    for info in result["v"]:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            raise bad
        ips.append(ip if ip.version == 6 and ip in _COMPAT96 else _unwrap(ip))
    allowed = host.lower().strip("[]") in _allow_hosts() or any(str(i) in _allow_hosts() for i in ips)
    for ip in ips:
        if _always_blocked(ip) or (not allowed and _ip_blocked(ip)):
            raise bad
    return [i.compressed for i in ips]


class _PinnedMixin:
    pinned: list = []
    deadline: float = 0.0

    def _dial(self):
        err = None
        for ip in self.pinned:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout()
            try:
                return socket.create_connection((ip, self.port), timeout=remaining)
            except socket.timeout:
                raise
            except OSError as e:
                err = e
        raise err or OSError("no address")


class _PinnedHTTP(_PinnedMixin, http.client.HTTPConnection):
    def connect(self):
        self.sock = self._dial()


class _PinnedHTTPS(_PinnedMixin, http.client.HTTPSConnection):
    def connect(self):
        raw = self._dial()
        raw.settimeout(max(self.deadline - time.monotonic(), 0.001))
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def fetch_card(url: str) -> dict:
    deadline = time.monotonic() + FETCH_TIMEOUT
    u = urlparse(url)
    try:
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        raise ExternalError(422, "card_url has an invalid port")
    if not host:
        raise ExternalError(422, "card_url must be an http or https URL")
    pinned = resolve_checked(host, port, deadline)
    cls = _PinnedHTTPS if u.scheme == "https" else _PinnedHTTP
    conn = cls(host, port, timeout=FETCH_TIMEOUT)
    conn.pinned, conn.deadline = pinned, deadline
    # Watchdog: unblocks any stalled connect/recv at the wall-clock deadline.
    expired = threading.Event()

    def kill():
        expired.set()
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
    timer = threading.Timer(FETCH_TIMEOUT, kill)
    timer.daemon = True
    timer.start()
    timeout_err = ExternalError(504, "timed out fetching the Agent Card (5 s)")
    try:
        try:
            path = (u.path or "/") + ("?" + u.query if u.query else "")
            conn.request("GET", path, headers={"Accept": "application/json"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise ExternalError(502, f"card URL returned HTTP {resp.status}")
            buf = b""
            while True:
                if time.monotonic() >= deadline:
                    raise timeout_err
                if conn.sock is not None:
                    conn.sock.settimeout(max(deadline - time.monotonic(), 0.001))
                chunk = resp.read1(1024)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_BYTES:
                    raise ExternalError(422, "card exceeds 1 MB")
            if expired.is_set() or time.monotonic() > deadline:
                raise timeout_err
        finally:
            timer.cancel()
            conn.close()
    except ExternalError:
        raise
    except (socket.timeout, TimeoutError):
        raise timeout_err
    except (OSError, http.client.HTTPException) as e:
        if expired.is_set() or time.monotonic() >= deadline:
            raise timeout_err
        log.info("agent card fetch failed: %s", type(e).__name__)
        raise ExternalError(502, "could not fetch the Agent Card from that address")
    try:
        card = json.loads(buf.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ExternalError(422, "card is not valid JSON")
    if not isinstance(card, dict):
        raise ExternalError(422, "card is not a JSON object")
    missing = [k for k in ("name", "version", "skills", "supportedInterfaces") if k not in card]
    if missing:
        raise ExternalError(422, f"not an A2A 1.0 Agent Card, missing: {', '.join(missing)}")
    if (not isinstance(card["name"], str) or not card["name"].strip() or not isinstance(card["skills"], list)
            or not isinstance(card["supportedInterfaces"], list)
            or any(not isinstance(s, dict) or not isinstance(s.get("id"), str) for s in card["skills"])):
        raise ExternalError(422, "not an A2A 1.0 Agent Card: invalid name, skills or supportedInterfaces")
    return card


# ---- JWS -------------------------------------------------------------------------------------

def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def trusted_keys() -> list:
    path = get_setting("trusted_jwks_path")
    if not path:
        return []
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    keys = data.get("keys", []) if isinstance(data, dict) and "keys" in data else [data]
    return [k for k in keys if isinstance(k, dict)]


def _public_key(jwk):
    n = lambda f: int.from_bytes(_unb64u(jwk[f]), "big")
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        return ec.EllipticCurvePublicNumbers(n("x"), n("y"), ec.SECP256R1()).public_key()
    if jwk.get("kty") == "RSA":
        return rsa.RSAPublicNumbers(n("e"), n("n")).public_key()
    return None


def _verify_one(sig, payload_b64, keys) -> bool:
    try:
        header = json.loads(_unb64u(sig["protected"]))
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ("ES256", "RS256") or not kid:
            return False
        raw = _unb64u(sig["signature"])
        signing_input = (sig["protected"] + "." + payload_b64).encode()
        for jwk in keys:
            if jwk.get("kid") != kid or (jwk.get("alg") not in (None, alg)):
                continue
            pub = _public_key(jwk)
            try:
                if alg == "ES256" and isinstance(pub, ec.EllipticCurvePublicKey):
                    if len(raw) != 64:
                        continue
                    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
                    pub.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
                    return True
                if alg == "RS256" and isinstance(pub, rsa.RSAPublicKey):
                    pub.verify(raw, signing_input, padding.PKCS1v15(), hashes.SHA256())
                    return True
            except InvalidSignature:
                continue
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return False


def verify_card(card: dict, keys=None) -> str:
    """'unsigned' when no signatures, 'signed' when one verifies against a trusted key, else 'invalid'."""
    sigs = card.get("signatures")
    if not sigs:
        return "unsigned"
    if not isinstance(sigs, list):
        return "invalid"
    body = copy.deepcopy(card)
    body.pop("signatures")
    payload_b64 = _b64u(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    keys = trusted_keys() if keys is None else keys
    return "signed" if any(isinstance(s, dict) and _verify_one(s, payload_b64, keys) for s in sigs) else "invalid"


# ---- tier and record -------------------------------------------------------------------------

def money_movement(card: dict) -> bool:
    for s in card["skills"]:
        text = [str(s.get("id", "")), str(s.get("name", ""))] + [str(t) for t in (s.get("tags") or [])]
        if any(w in t.lower() for t in text for w in MONEY_WORDS):
            return True
    return False


def apply_floor(tier: int, reasoning: str, card: dict):
    if money_movement(card):
        floored = min(tier, 2)
        note = ("Money-movement floor: a skill is tagged payments/transfer/wire/funds, so the tier is never "
                "lower-consequence than Tier 2")
        return floored, reasoning + " " + note + (f"; raised from Tier {tier} to Tier 2." if floored != tier else "; already Tier 2 or higher.")
    return tier, reasoning


def build_record(card_url: str, card: dict, criteria: dict, scopes, tier: int, reasoning: str,
                 signature: str, user) -> dict:
    now = datetime.now(timezone.utc)
    skills = [{**s, "id": s["id"], "name": str(s.get("name") or s["id"]),
               "tags": [str(t) for t in (s.get("tags") or [])]} for s in card["skills"]]
    tags = sorted({t for s in skills for t in s["tags"]})
    data_scopes = [str(x) for x in scopes] if isinstance(scopes, list) and scopes else tags
    org = (card.get("provider") or {}).get("organization") if isinstance(card.get("provider"), dict) else None
    ifaces = [i for i in card["supportedInterfaces"] if isinstance(i, dict) and i.get("url")]
    rec = {
        "id": external_id(card_url), "name": card["name"], "description": str(card.get("description") or card["name"]),
        "version": str(card["version"]),
        "provider": card["provider"] if isinstance(card.get("provider"), dict) else {"organization": "external"},
        "capabilities": card.get("capabilities") if isinstance(card.get("capabilities"), dict) else {},
        "securitySchemes": card.get("securitySchemes") if isinstance(card.get("securitySchemes"), dict) else {},
        "skills": skills, "oasf": {"skills": tags, "domains": []},
        "supportedInterfaces": card["supportedInterfaces"],
        "source_platform": "external", "source_native_id": card_url, "card_url": card_url,
        "last_seen": now.isoformat(), "owner": user.username or user.user_id,
        "tier": tier, "tier_reasoning": reasoning, "criteria": criteria,
        "manifest": {"tools": [s["id"] for s in skills], "data_scopes": data_scopes, "systems": [], "value_limits": {}},
        "identity": {"type": "a2a-card", "subject": card_url, "distinct": False},
        "dependencies": {"models": [], "mcp_servers": [], "tools": [{"name": s["id"]} for s in skills]},
        "status": "pending_review", "versions": [{"version": str(card["version"])}],
        "origin": "external", "card_signature": signature,
        "registered_by": user.user_id, "registered_by_username": user.username,
        "passport": {
            "identity": f"{org + ' / ' if org else ''}{card['name']}",
            "allowed_skills": [s["id"] for s in skills], "data_scopes": data_scopes,
            "expires_at": (now + timedelta(days=EXPIRY_DAYS.get(tier, 365))).isoformat(),
        },
    }
    if ifaces:
        rec["url"] = ifaces[0]["url"]
    return rec
