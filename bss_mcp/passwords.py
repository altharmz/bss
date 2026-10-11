"""The login users (MCP_USERS) and their password hashes - stdlib scrypt, no bcrypt.

MCP_USERS = "name:<hash>[,name:<hash>]", hash = "scrypt.<n>.<r>.<p>.<salt b64>.<key b64>".
No "$" in the format: compose would try to interpolate it in .env; base64url never
contains "." or ",".
The values live in .env / .env.prod only, never in the repo. A new hash:

    docker compose run --rm --no-deps app python -m bss_mcp.passwords
"""

import base64
import getpass
import hashlib
import hmac
import secrets
import sys

from bss_mcp.store import NAME

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


def parse_users(raw: str) -> dict[str, str]:
    """MCP_USERS -> {name: hash}. A malformed entry stops the service at start."""
    users: dict[str, str] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        name, sep, stored = entry.partition(":")
        if not sep or not NAME.match(name) or not stored.startswith("scrypt."):
            raise ValueError(f"MCP_USERS: malformed entry for {name!r} (expected name:scrypt....)")
        if name in users:
            raise ValueError(f"MCP_USERS: {name} appears twice")
        users[name] = stored
    return users


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


if __name__ == "__main__":
    first = getpass.getpass("password: ")
    if len(first) < 12:
        sys.exit("at least 12 characters")
    if getpass.getpass("again: ") != first:
        sys.exit("the two do not match")
    print(hash_password(first))
