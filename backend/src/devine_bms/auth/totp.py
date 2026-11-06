"""Authenticator-app codes (TOTP, RFC 6238): 6 digits, 30-second steps, SHA-1, as the apps expect."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_S = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, when: float | None = None) -> str:
    return _code(secret, int((time.time() if when is None else when) // STEP_S))


def verify(secret: str, code: str, when: float | None = None) -> bool:
    """Accept the current code and one step either side, for clock drift between phone and server."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return False
    counter = int((time.time() if when is None else when) // STEP_S)
    return any(hmac.compare_digest(_code(secret, counter + d), code) for d in (-1, 0, 1))


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """The otpauth:// address an authenticator app reads from a QR code."""
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_S}"
