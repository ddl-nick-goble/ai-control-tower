"""T-3 AC-11 data: signed agent card JWS verifies."""
import base64
import copy
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

EXT = Path(__file__).resolve().parent.parent / "fixtures" / "external"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify(card: dict, jwk: dict) -> bool:
    card = copy.deepcopy(card)
    sig = card.pop("signatures")[0]
    payload = json.dumps(card, sort_keys=True, separators=(",", ":")).encode()
    signing_input = (sig["protected"] + "." + b64u(payload)).encode()
    raw = unb64u(sig["signature"])
    if len(raw) != 64:
        return False
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    pub = ec.EllipticCurvePublicNumbers(
        int.from_bytes(unb64u(jwk["x"]), "big"),
        int.from_bytes(unb64u(jwk["y"]), "big"),
        ec.SECP256R1(),
    ).public_key()
    try:
        pub.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def test_ac11_signed_card_jws_verifies():
    card = json.loads((EXT / "agent-card-signed.json").read_text())
    jwk = json.loads((EXT / "signing-key.pub.jwk.json").read_text())
    assert card["signatures"] and "protected" in card["signatures"][0]
    hdr = json.loads(unb64u(card["signatures"][0]["protected"]))
    assert hdr["alg"] == "ES256" and hdr.get("kid") == jwk["kid"]
    assert _verify(card, jwk)

    tampered = copy.deepcopy(card)
    k = next(k for k, v in tampered.items() if isinstance(v, str) and k != "signatures")
    tampered[k] = tampered[k] + "x"
    assert not _verify(tampered, jwk)
