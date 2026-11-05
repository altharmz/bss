"""Deterministic stand-in for the network: confirms every command.

Test values (the line's MSISDN): ending `0000` is refused, ending `0001` fails
temporarily until `recover()` is called for it; a command kind put in `refuse_kinds` is
refused for every line. `generate_esim_profiles` is deterministic in the batch name
(IMSI `00199...`, ICCID `8999...`, SM-DP+ `smdp.stub.example`). The answer to a key is
remembered, so a repeated key gets the first answer and counts as one command in `received`.

Numbers: `request_numbers` hands out `1<NPA>5550100` to `...0199`, the range reserved for
fiction (100 per area code, then the pool is exhausted). Port-in (the request's values):
PIN `000000` is rejected `pinMismatch`, billing ZIP `00000` `zipMismatch` (both when the
carrier is polled); a number ending `0001` stays pending until `complete_port_in(number)`;
ending `0002` fails temporarily on submit until `recover(number)`. Port-out requests are put
in with `inject_port_out`; `port_answers` keeps what we answered.
"""

import hashlib
from typing import Any

from app.integrations.network.interface import (
    EsimProfile,
    NetworkRefused,
    NetworkTemporary,
    NumberPoolExhausted,
    PortOutRequest,
    PortStatus,
)

REFUSE_SUFFIX = "0000"
TEMPORARY_SUFFIX = "0001"
PORT_PENDING_SUFFIX = "0001"
PORT_TEMPORARY_SUFFIX = "0002"
POOL_SIZE = 100


class StubNetwork:
    def __init__(self) -> None:
        self.received: dict[str, tuple[str, dict[str, Any]]] = {}
        self.attempts: list[str] = []  # every call, repeated keys included
        self.recovered: set[str] = set()
        self.refuse_kinds: set[str] = set()
        self.pool_given: dict[str, int] = {}
        self.port_in: dict[str, dict[str, Any]] = {}  # key -> the request submitted
        self.port_completed: set[str] = set()
        self.port_out_queue: list[PortOutRequest] = []
        self.port_answers: dict[str, tuple[str, bool, str | None]] = {}

    def reset(self) -> None:
        self.received.clear()
        self.attempts.clear()
        self.recovered.clear()
        self.refuse_kinds.clear()
        self.pool_given.clear()
        self.port_in.clear()
        self.port_completed.clear()
        self.port_out_queue.clear()
        self.port_answers.clear()

    def complete_port_in(self, msisdn: str) -> None:
        self.port_completed.add(msisdn)

    def inject_port_out(self, reference: str, msisdn: str, pin: str) -> None:
        self.port_out_queue.append(PortOutRequest(reference, msisdn, pin))

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

    def generate_esim_profiles(self, batch: str, count: int) -> list[EsimProfile]:
        digest = int(hashlib.sha256(batch.encode()).hexdigest(), 16)
        tag = f"{digest % 10**6:06d}"
        return [
            EsimProfile(
                iccid=f"8999{tag}{index:08d}",
                imsi=f"00199{tag}{index:04d}",
                smdp_address="smdp.stub.example",
                matching_id=hashlib.sha256(f"{batch}:{index}".encode()).hexdigest()[:16].upper(),
            )
            for index in range(count)
        ]

    def request_numbers(self, area_code: str, count: int) -> list[str]:
        given = self.pool_given.get(area_code, 0)
        if given + count > POOL_SIZE:
            raise NumberPoolExhausted(f"stub: no more numbers for area code {area_code}")
        self.pool_given[area_code] = given + count
        return [f"1{area_code}555{100 + given + n:04d}" for n in range(count)]

    def submit_port_in(self, key: str, request: dict[str, Any]) -> PortStatus:
        self.attempts.append(key)
        msisdn = request["msisdn"]
        if key not in self.port_in:
            if msisdn.endswith(PORT_TEMPORARY_SUFFIX) and msisdn not in self.recovered:
                raise NetworkTemporary(f"stub: the carrier is unavailable for {msisdn}")
            self.port_in[key] = request
        return PortStatus("pending", reference=f"PORT-{key}")

    def port_in_status(self, reference: str) -> PortStatus:
        request = self.port_in[reference.removeprefix("PORT-")]
        msisdn = request["msisdn"]
        if request.get("transferPin") == "000000":
            return PortStatus(
                "rejected", reference, "pinMismatch", "the transfer PIN does not match"
            )
        if request.get("billingZip") == "00000":
            return PortStatus(
                "rejected", reference, "zipMismatch", "the billing ZIP does not match"
            )
        if msisdn.endswith(PORT_PENDING_SUFFIX) and msisdn not in self.port_completed:
            return PortStatus("pending", reference)
        return PortStatus("completed", reference)

    def port_out_requests(self) -> list[PortOutRequest]:
        return list(self.port_out_queue)

    def answer_port_out(
        self, key: str, reference: str, approved: bool, reason: str | None = None
    ) -> None:
        self.port_answers.setdefault(key, (reference, approved, reason))
        self.port_out_queue = [r for r in self.port_out_queue if r.reference != reference]
