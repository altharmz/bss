"""The network (T-Mobile) provisioning adapter: the interface and its stub.

`BSS_NETWORK_ADAPTER` selects the implementation at startup (`stub`, the default); an
unknown value fails startup (app/config.py) and `adapter()`.
"""

from app.config import settings
from app.integrations.network.interface import (
    COMMANDS,
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


def adapter() -> NetworkAdapter:
    name = settings.network_adapter
    if name not in _ADAPTERS:
        if name != "stub":
            raise ValueError(f"unknown BSS_NETWORK_ADAPTER {name!r}")
        from app.integrations.network.stub import StubNetwork

        _ADAPTERS[name] = StubNetwork()
    return _ADAPTERS[name]
