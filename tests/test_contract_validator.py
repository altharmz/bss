"""The contract validator itself: it must resolve allOf and honour discriminators.

Proven on TMF620 ProductOfferingPriceRefOrValue - a oneOf of ProductOfferingPrice (only
`@type` required) and ProductOfferingPriceRef (`@type` and `id` required). A plain
reference satisfies both branches, so only the discriminator can pick one.
"""

import jsonschema
import pytest

from tests import contract

REF = {"@type": "ProductOfferingPriceRef", "id": "42", "href": "/x/42", "name": "Monthly"}
VALUE = {
    "@type": "ProductOfferingPrice",
    "name": "Monthly",
    "priceType": "recurring",
    "price": {"unit": "RUB", "value": 19.99},
}


def _errors(instance) -> list[str]:
    return contract.errors("TMF620", "ProductOfferingPriceRefOrValue", instance)


def test_discriminator_accepts_a_ref_and_a_value():
    assert _errors(REF) == []
    assert _errors(VALUE) == []


def test_plain_jsonschema_cannot_validate_the_same_ref():
    """Why the OAS-aware validator is needed: without the discriminator, oneOf is ambiguous."""
    doc = contract.spec("TMF620")
    schema = {"$ref": "#/components/schemas/ProductOfferingPriceRefOrValue", **doc}
    found = [e.message for e in jsonschema.Draft4Validator(schema).iter_errors(REF)]
    assert len(found) == 1
    assert "valid under each of" in found[0]


def test_library_discriminator_handling_recurses_on_tmf_schemas():
    """Why contract.TmfValidator overrides it: upstream dispatches on the self-mapping
    discriminator next to allOf and never returns."""
    from openapi_schema_validator import OAS30Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT4

    doc = contract.spec("TMF620")
    registry = Registry().with_resource("urn:x", Resource(contents=doc, specification=DRAFT4))
    upstream = OAS30Validator({"$ref": "urn:x#/components/schemas/Error"}, registry=registry)
    with pytest.raises(RecursionError):
        list(upstream.iter_errors({"@type": "Error", "code": "404", "reason": "Not Found"}))


def test_discriminator_picks_the_branch_named_by_type():
    # Without `id` this would still be a valid ProductOfferingPrice (which requires only
    # @type) - it is rejected because @type selects the Ref branch, where id is mandatory.
    no_id = {k: v for k, v in REF.items() if k != "id"}
    assert any("'id' is a required property" in e for e in _errors(no_id))


def test_missing_discriminator_property_is_rejected():
    no_type = {k: v for k, v in REF.items() if k != "@type"}
    assert any("@type" in e for e in _errors(no_type))


def test_unknown_discriminator_value_is_rejected():
    assert _errors({**REF, "@type": "NoSuchThing"}) != []


def test_allof_is_resolved_down_to_nested_money():
    # price -> Money.value is declared three $ref/allOf levels below the oneOf branch
    broken = {**VALUE, "price": {"unit": "RUB", "value": "19.99"}}
    assert any(e.startswith("price/value:") and "number" in e for e in _errors(broken))


def test_wrong_type_in_an_inherited_property_is_rejected():
    # `href` comes from the Entity base schema via allOf
    assert any(e.startswith("href:") for e in _errors({**VALUE, "href": 5}))


@pytest.mark.parametrize("api", sorted(contract.SPEC_FILES))
def test_error_schema_requires_code_and_reason(api):
    body = {"@type": "Error", "code": "404", "reason": "Not Found"}
    assert contract.errors(api, "Error", body) == []
    assert contract.errors(api, "Error", {"@type": "Error", "code": "404"}) != []
