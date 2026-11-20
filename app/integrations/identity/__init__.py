"""The identity check adapter (placeholder): the interface and its stub.

`BSS_IDENTITY_ADAPTER` selects the implementation at startup (`stub`, the default); an unknown
value fails startup (app/config.py) and `adapter()`. The worker sends the checks
(app/worker/signup.py), never a request. docs/decisions/self-care.md "Online signup".
"""

from app.config import settings
from app.integrations.identity.interface import (
    IdentityAdapter,
    IdentityResult,
    IdentityTemporary,
)

__all__ = ["IdentityAdapter", "IdentityResult", "IdentityTemporary", "adapter"]

_ADAPTERS: dict[str, IdentityAdapter] = {}


def adapter() -> IdentityAdapter:
    name = settings.identity_adapter
    if name not in _ADAPTERS:
        if name != "stub":
            raise ValueError(f"unknown BSS_IDENTITY_ADAPTER {name!r}")
        from app.integrations.identity.stub import StubIdentity

        _ADAPTERS[name] = StubIdentity()
    return _ADAPTERS[name]
