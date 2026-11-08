"""SCRAM SHA-256 for Haystack authentication (RFC 5802; Haystack auth, milestone 12a-Haystack).

Only the salted keys are kept: StoredKey = H(HMAC(SaltedPassword, "Client Key")) and
ServerKey = HMAC(SaltedPassword, "Server Key"), with SaltedPassword = PBKDF2-SHA-256(password,
salt, i). The password itself is never stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ITERATIONS = 10_000


def b64(data: bytes) -> str:
    """Standard base64 without padding (what Haystack clients such as phable read)."""
    return base64.b64encode(data).decode().rstrip("=")


def unb64(text: str) -> bytes:
    """Standard or URL-safe base64, with or without padding."""
    text = text.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)


def _hmac(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def make_keys(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> dict:
    salt = salt or secrets.token_bytes(18)  # 18 bytes: 24 base64 characters, no padding needed
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return {"salt": b64(salt), "i": iterations,
            "storedKey": b64(hashlib.sha256(_hmac(salted, b"Client Key")).digest()),
            "serverKey": b64(_hmac(salted, b"Server Key"))}


def check_proof(keys: dict, auth_message: str, proof_b64: str) -> bool:
    """The client's proof: ClientKey = proof XOR HMAC(StoredKey, AuthMessage); H(ClientKey) must be StoredKey."""
    try:
        stored, proof = unb64(keys["storedKey"]), unb64(proof_b64)
    except (KeyError, ValueError):
        return False
    signature = _hmac(stored, auth_message.encode())
    if len(proof) != len(signature):
        return False
    client_key = bytes(a ^ b for a, b in zip(proof, signature, strict=True))
    return hmac.compare_digest(hashlib.sha256(client_key).digest(), stored)


def server_signature(keys: dict, auth_message: str) -> str:
    """Padded base64, as RFC 5802 writes values inside the SCRAM messages."""
    return base64.b64encode(_hmac(unb64(keys["serverKey"]), auth_message.encode())).decode()


def fake_keys(secret: bytes, username: str) -> dict:
    """For an unknown user: a salt that is always the same for that name, so the exchange looks
    the same as for a real account and fails at the proof."""
    salt = _hmac(secret, b"salt:" + username.encode())[:18]
    return {"salt": b64(salt), "i": ITERATIONS, "storedKey": b64(secrets.token_bytes(32)),
            "serverKey": b64(secrets.token_bytes(32))}
