"""The registered E911 address of a Wi-Fi calling line (docs/decisions/network.md#e911-address).

TMF637/TMF622 `product.place`: one `RelatedPlaceRefOrValue` with role `e911Address` and a
`GeographicAddress` (streetNr, streetName, streetSuffix, city, stateOrProvince, postcode,
country, geographicSubAddress). It is validated as the account's service address is (US only,
a state or territory code, a ZIP or ZIP+4, a street and a city) and stored normalized in
`product.e911_address`. Any other role is ours to refuse: we implement only this one.
"""

from typing import Any

from app.billing.engine_tax import STATES, ZIP

ROLE = "e911Address"
FLAG = "wifiCalling"  # the network profile flag of a line that can make Wi-Fi calls
TEXT = ("streetNr", "streetName", "streetSuffix", "city")


def wifi_calling(profile: dict[str, Any] | None) -> bool:
    return (profile or {}).get(FLAG) == 1


def role_problem(places: Any) -> str | None:
    """Why `place` is not one entry of role e911Address (a 400: no other role exists)."""
    if not isinstance(places, list) or len(places) != 1:
        return "place: one entry of role e911Address is expected"
    entry = places[0]
    if not isinstance(entry, dict) or entry.get("role") != ROLE:
        return "place: only the role e911Address is supported"
    return None


def address_problems(place: Any, path: str) -> list[tuple[str, str]]:
    """(path, message) for what is wrong with the GeographicAddress of an e911Address place."""
    if not isinstance(place, dict) or place.get("@type") != "GeographicAddress":
        return [(path, "the E911 address is a GeographicAddress value (@type GeographicAddress)")]
    found = []
    for name in ("streetName", "city"):
        if not isinstance(place.get(name), str) or not place[name].strip():
            found.append((f"{path}.{name}", f"{name} is required"))
    for name in ("streetNr", "streetSuffix"):
        if name in place and (not isinstance(place[name], str) or not place[name].strip()):
            found.append((f"{path}.{name}", f"{name} must not be empty"))
    if place.get("country") not in ("US", "USA"):
        found.append((f"{path}.country", "country must be US"))
    if place.get("stateOrProvince") not in STATES:
        found.append((f"{path}.stateOrProvince", "stateOrProvince must be a US state code"))
    if not isinstance(place.get("postcode"), str) or not ZIP.match(place["postcode"]):
        found.append((f"{path}.postcode", "postcode must be a ZIP (12345) or ZIP+4 (12345-6789)"))
    return found


def normalized(place: dict[str, Any]) -> dict[str, Any]:
    """The stored form of a valid address: trimmed text, country US, the sub-address as sent."""
    out = {name: place[name].strip() for name in TEXT if isinstance(place.get(name), str)}
    out |= {"stateOrProvince": place["stateOrProvince"], "postcode": place["postcode"]}
    out["country"] = "US"
    if place.get("geographicSubAddress"):
        out["geographicSubAddress"] = place["geographicSubAddress"]
    return out


def requested(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """The normalized address an order item carries (checked by the API), or None."""
    places = ((details or {}).get("product") or {}).get("place")
    if places is None or role_problem(places) is not None:
        return None
    place = places[0].get("place")
    return None if address_problems(place, "place") else normalized(place)


def from_service_address(address: dict[str, Any]) -> dict[str, Any]:
    """The default: the account's service address (TMF666 street1/street2/postCode) copied."""
    out = {
        "streetName": address["street1"],
        "city": address["city"],
        "stateOrProvince": address["stateOrProvince"],
        "postcode": address["postCode"],
        "country": "US",
    }
    if address.get("street2"):
        out["geographicSubAddress"] = [
            {"name": address["street2"], "@type": "GeographicSubAddress"}
        ]
    return out


def place_body(address: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """TMF637 `product.place` of a stored address."""
    if address is None:
        return None
    return [
        {
            "role": ROLE,
            "place": {**address, "@type": "GeographicAddress"},
            "@type": "RelatedPlaceRefOrValue",
        }
    ]
