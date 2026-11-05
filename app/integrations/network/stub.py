"""Deterministic stand-in for the network: confirms every command.

Test values (the line's MSISDN): ending `0000` is refused, ending `0001` fails
temporarily until `recover()` is called for it; a command kind put in `refuse_kinds` is
refused for every line. The answer to a key is remembered, so a
repeated key gets the first answer and counts as one command in `received`.
"""

from typing import Any

from app.integrations.network.interface import NetworkRefused, NetworkTemporary

REFUSE_SUFFIX = "0000"
TEMPORARY_SUFFIX = "0001"


class StubNetwork:
    def __init__(self) -> None:
        self.received: dict[str, tuple[str, dict[str, Any]]] = {}
        self.attempts: list[str] = []  # every call, repeated keys included
        self.recovered: set[str] = set()
        self.refuse_kinds: set[str] = set()

    def reset(self) -> None:
        self.received.clear()
        self.attempts.clear()
        self.recovered.clear()
        self.refuse_kinds.clear()

    def recover(self, msisdn: str) -> None:
        self.recovered.add(msisdn)

    def send(self, key: str, command: str, request: dict[str, Any]) -> None:
        self.attempts.append(key)
        msisdn = request.get("msisdn") or ""
        if key in self.received:
            return
        if msisdn.endswith(REFUSE_SUFFIX) or command in self.refuse_kinds:
            raise NetworkRefused(f"stub: the network refused {command} for {msisdn}", "stubRefused")
        if msisdn.endswith(TEMPORARY_SUFFIX) and msisdn not in self.recovered:
            raise NetworkTemporary(f"stub: the network is unavailable for {msisdn}")
        self.received[key] = (command, request)
