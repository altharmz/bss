"""The `log` sender: records every message it is given and accepts it.

Deterministic, no network. Test values: an SMS to a number ending `0000` or an email to
`fail@example.test` is not accepted (NotifyError) until `recover(to)` is called. A repeated
key answers the first reference without recording the message again.
"""

import logging
from dataclasses import dataclass

from app.integrations.notify.interface import NotifyError

log = logging.getLogger("app.notify")


@dataclass(frozen=True)
class Sent:
    key: str
    channel: str
    to: str
    subject: str | None
    body: str


class LogNotify:
    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self.attempts: list[tuple[str, str]] = []  # (channel, to) of every call
        self._refs: dict[str, str] = {}
        self._recovered: set[str] = set()

    def reset(self) -> None:
        self.__init__()

    def recover(self, to: str) -> None:
        self._recovered.add(to)

    def _send(self, key: str, channel: str, to: str, subject: str | None, body: str) -> str:
        self.attempts.append((channel, to))
        if key in self._refs:
            return self._refs[key]
        bad = to.endswith("0000") if channel == "sms" else to == "fail@example.test"
        if bad and to not in self._recovered:
            raise NotifyError(f"the sender refused {to}")
        self.sent.append(Sent(key, channel, to, subject, body))
        log.info("notify %s to %s: %s", channel, to, body)
        self._refs[key] = f"log-{len(self.sent)}"
        return self._refs[key]

    def send_sms(self, key: str, to: str, body: str) -> str:
        return self._send(key, "sms", to, None, body)

    def send_email(self, key: str, to: str, subject: str, body: str) -> str:
        return self._send(key, "email", to, subject, body)
