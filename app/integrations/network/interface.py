"""The contract between the BSS and the network.

A command is sent with an idempotency key (the id of the BSS command): the network must
answer a repeated key with the first answer. `send` returns when the network confirms,
raises `NetworkRefused` when it refuses (final) and `NetworkTemporary` when the answer is
not known or the network could not be reached (the BSS retries with the same key).
"""

from typing import Any, Protocol

COMMANDS = ("activate", "suspend", "restore", "deactivate", "change_profile")


class NetworkRefused(Exception):
    """The network refused the command: final, the order item fails."""

    def __init__(self, reason: str, code: str = "networkRefused"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


class NetworkTemporary(Exception):
    """A transport or temporary error: the same command is sent again later."""


class NetworkAdapter(Protocol):
    def send(self, key: str, command: str, request: dict[str, Any]) -> None: ...
