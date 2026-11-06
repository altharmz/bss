"""The contract between the BSS and the SMS and email sender.

A message is sent with an idempotency key (the id of the BSS message): the sender must
answer a repeated key with the first answer and never send twice. `send_*` return the
sender's reference of the accepted message and raise `NotifyError` when it was not
accepted (the BSS retries with the same key).
"""

from typing import Protocol


class NotifyError(Exception):
    """The sender did not accept the message: the same message is sent again later."""


class NotifyAdapter(Protocol):
    def send_sms(self, key: str, to: str, body: str) -> str: ...

    def send_email(self, key: str, to: str, subject: str, body: str) -> str: ...
