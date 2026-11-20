"""The network profile of an offering (docs/decisions/network.md).

Held as TMF620 characteristics, no table of its own: the product specification declares
`productSpecCharacteristic` entries named like PROFILE (the values the offerings may use,
the default first; every value is an integer, a flag is 0 or 1 because the vendored
TMF620 schema does not validate boolean values), and an offering narrows a value with a
`prodSpecCharValueUse` entry of the same name. A specification with any of these names
makes its offerings network lines; `effective()` is what the network is sent.
"""

from typing import Any

PROFILE: dict[str, str] = {
    "dataAllowanceMb": "integer",
    "dataCapMb": "integer",
    "throttleKbps": "integer",
    "voiceMinutes": "integer",
    "smsCount": "integer",
    "hotspot": "flag",
    "international": "flag",
    "voicemail": "flag",
    # the line can make Wi-Fi calls: it needs an E911 address (#201, docs/decisions/network.md)
    "wifiCalling": "flag",
    # roaming and international passes (#200, docs/decisions/network.md)
    "roaming": "flag",
    "roamingDataMb": "integer",
    "roamingMinutes": "integer",
    "roamingSmsCount": "integer",
    "internationalMinutes": "integer",
    # the line shares one data allowance with the account's other pooled lines (#379,
    # docs/decisions/usage.md "Pooled data")
    "dataPool": "flag",
}


def _valid(value_type: str, value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value in (0, 1) if value_type == "flag" else value >= 0


def _values(entry: dict[str, Any]) -> list[Any]:
    return [v.get("value") for v in entry.get("characteristicValueSpecification") or []]


def _spec_entries(spec_details: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    entries = (spec_details or {}).get("productSpecCharacteristic") or []
    return {e["name"]: e for e in entries if isinstance(e, dict) and e.get("name") in PROFILE}


def is_line(spec_details: dict[str, Any] | None) -> bool:
    return bool(_spec_entries(spec_details))


def spec_problems(spec_details: dict[str, Any]) -> list[str]:
    found = []
    for name, entry in _spec_entries(spec_details).items():
        kind = PROFILE[name]
        shown = "integer"
        specs = entry.get("characteristicValueSpecification") or []
        if not specs:
            found.append(f"{name}: at least one characteristicValueSpecification is needed")
        for spec in specs:
            if spec.get("valueType", shown) != shown or not _valid(kind, spec.get("value")):
                found.append(
                    f"{name}: the network profile takes "
                    + ("0 or 1" if kind == "flag" else "integers of zero or more")
                )
                break
    return found


def offering_problems(spec_details: dict[str, Any] | None, offering_details: dict[str, Any]):
    entries = _spec_entries(spec_details)
    found = []
    for use in offering_details.get("prodSpecCharValueUse") or []:
        name = use.get("name") if isinstance(use, dict) else None
        if name not in PROFILE:
            continue
        if name not in entries:
            found.append(f"{name}: the productSpecification declares no such characteristic")
            continue
        allowed = _values(entries[name])
        given = [v.get("value") for v in use.get("productSpecCharacteristicValue") or []]
        if len(given) != 1 or given[0] not in allowed or not _valid(PROFILE[name], given[0]):
            found.append(f"{name}: one value of the specification's {allowed} is expected")
    return found


def effective(
    spec_details: dict[str, Any] | None, offering_details: dict[str, Any] | None
) -> dict[str, Any]:
    """The specification's default of each profile characteristic, narrowed by the offering."""
    out = {}
    for name, entry in _spec_entries(spec_details).items():
        specs = entry.get("characteristicValueSpecification") or []
        default = next((s for s in specs if s.get("isDefault")), specs[0] if specs else None)
        if default is not None:
            out[name] = default.get("value")
    for use in (offering_details or {}).get("prodSpecCharValueUse") or []:
        values = use.get("productSpecCharacteristicValue") or [] if isinstance(use, dict) else []
        if isinstance(use, dict) and use.get("name") in out and values:
            out[use["name"]] = values[0].get("value")
    return out


ADDITIVE = (
    "dataAllowanceMb",
    "dataCapMb",
    "voiceMinutes",
    "smsCount",
    "roamingDataMb",
    "roamingMinutes",
    "roamingSmsCount",
    "internationalMinutes",
)


def combine(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """The profile of a line with an add-on pack: the pack's allowances added to the plan's
    (data, cap, minutes, messages, the roaming and international allowances), its flags
    switched on, the throttle speed kept."""
    out = dict(base)
    for name, value in extra.items():
        if name in ADDITIVE:
            out[name] = out.get(name, 0) + value
        elif PROFILE.get(name) == "flag":
            out[name] = max(out.get(name, 0), value)
        else:
            out.setdefault(name, value)
    return out
