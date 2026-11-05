"""The contract between the BSS and the network.

A command is sent with an idempotency key (the id of the BSS command): the network must
answer a repeated key with the first answer. `send` returns when the network confirms,
raises `NetworkRefused` when it refuses (final) and `NetworkTemporary` when the answer is
not known or the network could not be reached (the BSS retries with the same key).
"""

from dataclasses import dataclass
from typing import Any, Protocol

COMMANDS = ("activate", "suspend", "restore", "deactivate", "change_profile", "swap_sim")


@dataclass(frozen=True)
class EsimProfile:
    """A generated eSIM profile: identities and activation code parts. Never Ki or OPc."""

    iccid: str
    imsi: str
    smdp_address: str
    matching_id: str


@dataclass(frozen=True)
class PortStatus:
    """The carrier's view of a port-in: `pending`, `completed` or `rejected` (code, reason)."""

    state: str
    reference: str | None = None
    code: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PortOutRequest:
    """A port-out the carrier reports: its own reference, the number and the PIN given."""

    reference: str
    msisdn: str
    pin: str


class NumberPoolExhausted(Exception):
    """The carrier has no more numbers for the area code."""


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

    def generate_esim_profiles(self, batch: str, count: int) -> list[EsimProfile]:
        """Ask the network for `count` fresh eSIM profiles (an operator request: nothing
        of a line changes; a real adapter may later make this asynchronous)."""
        ...

    def request_numbers(self, area_code: str, count: int) -> list[str]:
        """Ask the carrier for `count` numbers of the area code (E.164 digits, with the
        country code). Raises `NumberPoolExhausted`, or `NetworkTemporary` when unavailable."""
        ...

    def submit_port_in(self, key: str, request: dict[str, Any]) -> PortStatus:
        """Submit a port-in (idempotent on `key`); the answer is usually `pending`."""
        ...

    def port_in_status(self, reference: str) -> PortStatus: ...

    def port_out_requests(self) -> list[PortOutRequest]:
        """New port-outs the carrier reports (each is returned until `answer_port_out`
        acknowledges it; the BSS ignores a reference it already recorded)."""
        ...

    def answer_port_out(
        self, key: str, reference: str, approved: bool, reason: str | None = None
    ) -> None:
        """Our answer to a port-out request, idempotent on `key`."""
        ...
