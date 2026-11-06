"""The customer notification sender (SMS and email placeholder): the interface and its `log`
implementation. `BSS_NOTIFY_ADAPTER` selects it at startup (`log`, the default); an unknown
value fails startup (app/config.py) and `adapter()`. docs/decisions/notifications.md.
"""

from app.config import settings
from app.integrations.notify.interface import NotifyAdapter, NotifyError

__all__ = ["NotifyAdapter", "NotifyError", "adapter"]

_ADAPTERS: dict[str, NotifyAdapter] = {}


def adapter() -> NotifyAdapter:
    name = settings.notify_adapter
    if name not in _ADAPTERS:
        if name != "log":
            raise ValueError(f"unknown BSS_NOTIFY_ADAPTER {name!r}")
        from app.integrations.notify.stub import LogNotify

        _ADAPTERS[name] = LogNotify()
    return _ADAPTERS[name]
