"""Password hashing for the app's users (app/users.py) - stdlib scrypt, no bcrypt.

Hash format: "scrypt.<n>.<r>.<p>.<salt b64>.<key b64>" (no "$": base64url never contains
"." either). The hashes live in the app_user table only.
"""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1  # 16 MiB per check
_DUMMY = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P)
    return f"scrypt.{N}.{R}.{P}.{_b64(salt)}.{_b64(key)}"


def check_password(password: str, stored: str | None) -> bool:
    """Constant-time; an unknown user (stored=None) costs the same as a known one."""
    global _DUMMY
    if stored is None:
        _DUMMY = _DUMMY or hash_password(secrets.token_hex(8))
        stored, known = _DUMMY, False
    else:
        known = True
    try:
        scheme, n, r, p, salt, key = stored.split(".")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p)
        )
    except ValueError:
        return False
    return hmac.compare_digest(candidate, _unb64(key)) and known


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
