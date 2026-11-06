"""Password hashing with scrypt (Python standard library), a random salt per password."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**15, 8, 1  # about 32 MB and well under a second on a Pi 4
_MAXMEM = 64 * 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                maxmem=_MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A hash to check against when the user does not exist, so a wrong user name takes as long as a
# wrong password (it does not reveal which accounts exist).
DUMMY_HASH = hash_password(secrets.token_hex(16))
