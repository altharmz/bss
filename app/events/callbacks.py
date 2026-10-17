"""Which callback URLs a hub may use, and where they may lead.

The service runs next to a VPN server and other services on a shared Docker network, so
a callback is an outbound request on behalf of whoever created the hub. Outside dev and
test (APP_ENV), a callback must be https and its host must resolve only to public
addresses - no loopback, private, link-local, shared, reserved or multicast ranges.
Checked when the hub is created and again before every delivery (DNS can change).
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from app.config import settings

Resolver = Callable[[str, int], list[str]]

LOCAL_ENVS = frozenset({"dev", "test"})


class CallbackRefused(ValueError):
    """The callback cannot be used; the message says why."""


def private_allowed() -> bool:
    return settings.app_env in LOCAL_ENVS


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CallbackRefused(f"callback host {host} does not resolve ({exc})") from None
    return sorted({info[4][0] for info in infos})


def check(
    url: str, *, resolver: Resolver | None = None, allow_private: bool | None = None
) -> list[str]:
    """The addresses the callback leads to (CallbackRefused if it may not be used). With
    private addresses allowed (dev, test) nothing is resolved and the list is empty."""
    allow = private_allowed() if allow_private is None else allow_private
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CallbackRefused("callback must be an absolute http(s) URL")
    if parts.username is not None or parts.password is not None:
        raise CallbackRefused("callback must not carry credentials")
    if parts.fragment:
        raise CallbackRefused("callback must not have a fragment")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise CallbackRefused("callback has an invalid port") from None
    if allow:
        return []
    if parts.scheme != "https":
        raise CallbackRefused("callback must use https")
    addresses = (resolver or resolve)(parts.hostname, port)
    if not addresses:
        raise CallbackRefused(f"callback host {parts.hostname} does not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is loopback, whatever is_global says
        if not ip.is_global or ip.is_multicast:
            raise CallbackRefused(
                f"callback host {parts.hostname} resolves to {address}, which is not a public"
                " address"
            )
    return addresses
