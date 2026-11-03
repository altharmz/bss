"""D5: categories and catalogs. "Mobile Plans" and "Consumer Catalog 2026" are reused when
present; "Home Internet", "TV" and "Business Catalog 2026" are created, and every active
offering is assigned to a category by its specification's name. A second run finds
nothing to do.

    BSS_URL=... BSS_USER=... BSS_PASSWORD=... python3 catalog_tree.py [--apply]
"""

from typing import Any

from _client import CATALOG, Client, run

CATEGORIES = f"{CATALOG}/category"
CATALOGS = f"{CATALOG}/productCatalog"
MOBILE, INTERNET, TV = "Mobile Plans", "Home Internet", "TV"
CONSUMER, BUSINESS = "Consumer Catalog 2026", "Business Catalog 2026"
CATALOG_CATEGORIES = {CONSUMER: [MOBILE, INTERNET, TV], BUSINESS: [MOBILE, INTERNET]}


def category_for(specification: str) -> str | None:
    """The category an offering belongs to, from its specification's name."""
    name = specification.lower()
    if "mobile" in name:
        return MOBILE
    if "internet" in name:
        return INTERNET
    if name == "tv" or name.startswith("tv "):
        return TV
    return None


def ids(refs: list[dict[str, Any]] | None) -> list[str]:
    return [r["id"] for r in refs or []]


def ref(kind: str, record_id: str) -> dict[str, str]:
    return {"id": record_id, "@type": kind}


def work(client: Client) -> None:
    by_name = {c["name"]: c for c in client.all(CATEGORIES)}
    for name in (MOBILE, INTERNET, TV):
        if name not in by_name:
            created = client.post(
                CATEGORIES,
                {"@type": "Category", "name": name, "lifecycleStatus": "Active"},
                f"create category {name!r}",
            )
            by_name[name] = created or {"id": f"<new {name}>", "name": name}

    specs = {s["id"]: s["name"] for s in client.all(f"{CATALOG}/productSpecification")}
    members: dict[str, list[str]] = {
        name: ids(by_name[name].get("productOffering")) for name in by_name
    }
    offerings = client.all(f"{CATALOG}/productOffering?lifecycleStatus=Active")
    changed: list[str] = []
    for offering in offerings:
        spec = (offering.get("productSpecification") or {}).get("id")
        name = category_for(specs.get(spec, ""))
        if name is None:
            print(f"skip: offering {offering['name']!r} has no category for its specification")
            continue
        if offering["id"] not in members[name]:
            members[name] = [*members[name], offering["id"]]
            if name not in changed:
                changed.append(name)
    for name in changed:
        offering_ids = members[name]
        client.patch(
            f"{CATEGORIES}/{by_name[name]['id']}",
            {"productOffering": [ref("ProductOfferingRef", i) for i in offering_ids]},
            f"category {name!r}: {len(offering_ids)} offerings",
        )

    catalogs = {c["name"]: c for c in client.all(CATALOGS)}
    for name, roots in CATALOG_CATEGORIES.items():
        wanted = [by_name[r]["id"] for r in roots]
        if name not in catalogs:
            client.post(
                CATALOGS,
                {
                    "@type": "ProductCatalog",
                    "name": name,
                    "lifecycleStatus": "Active",
                    "category": [ref("CategoryRef", i) for i in wanted],
                },
                f"create catalog {name!r} with {', '.join(roots)}",
            )
        elif missing := [i for i in wanted if i not in ids(catalogs[name].get("category"))]:
            client.patch(
                f"{CATALOGS}/{catalogs[name]['id']}",
                {
                    "category": [
                        ref("CategoryRef", i) for i in ids(catalogs[name].get("category")) + missing
                    ]
                },
                f"catalog {name!r}: add {len(missing)} categories",
            )


if __name__ == "__main__":
    raise SystemExit(run(__doc__.split("\n\n")[0], work))
