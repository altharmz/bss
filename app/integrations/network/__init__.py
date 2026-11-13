"""The network (T-Mobile) provisioning adapter: the interface and its stub.

`BSS_NETWORK_ADAPTER` selects the implementation at startup (`stub`, the default); an
unknown value fails startup (app/config.py) and `adapter()`.
"""

from app.config import settings
from app.integrations.network.interface import (
    COMMANDS,
    DeviceCheck,
    EsimProfile,
    NetworkAdapter,
    NetworkRefused,
    NetworkTemporary,
    NumberPoolExhausted,
    PortOutRequest,
    PortStatus,
    UsageRecord,
)

__all__ = [
    "COMMANDS",
    "DeviceCheck",
    "EsimProfile",
    "NetworkAdapter",
    "NetworkRefused",
    "NetworkTemporary",
    "NumberPoolExhausted",
    "PortOutRequest",
    "PortStatus",
    "UsageRecord",
    "adapter",
]

_ADAPTERS: dict[str, NetworkAdapter] = {}


def _usage_load():  # noqa: ANN202 - imported lazily, like the stub
    """The load-test usage generator when BSS_STUB_USAGE_RATE is set (refused unless
    APP_ENV=test), else None."""
    if not settings.stub_usage_rate:
        return None
    from functools import partial
    from zoneinfo import ZoneInfo

    from app.clock import SystemClock
    from app.integrations.network.usage_load import UsageLoad, assigned_numbers

    return UsageLoad(
        settings.stub_usage_rate,
        app_env=settings.app_env,
        clock=SystemClock(),
        tz=ZoneInfo(settings.billing_tz),
        numbers=partial(assigned_numbers, settings.database_url),
        hour=None if settings.stub_usage_hour < 0 else settings.stub_usage_hour,
    )


def adapter() -> NetworkAdapter:
    name = settings.network_adapter
    if name not in _ADAPTERS:
        if name != "stub":
            raise ValueError(f"unknown BSS_NETWORK_ADAPTER {name!r}")
        from app.integrations.network.stub import StubNetwork

        _ADAPTERS[name] = StubNetwork(_usage_load())
    return _ADAPTERS[name]
