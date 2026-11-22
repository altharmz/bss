"""Shared builders of tests/test_api_financial_account.py, used by other test modules (#423)."""

FINANCIAL = "/tmf-api/accountManagement/v5/financialAccount"


def owner(party, **over):
    return {
        "role": "owner",
        "partyOrPartyRole": {
            "id": party["id"],
            "href": party["href"],
            "@type": "PartyRef",
            "@referredType": party["@type"],
        },
        "@type": "RelatedPartyRefOrPartyRoleRef",
        **over,
    }


def financial(api, party, **over):
    body = {
        "@type": "FinancialAccount",
        "name": "Group receivables",
        "relatedParty": [owner(party)],
    }
    response = api.post(FINANCIAL, {**body, **over})
    assert response.status_code == 201, response.text
    assert response.headers["location"] == response.json()["href"]
    return response.json()


def link(fa):
    return {"id": fa["id"], "@type": "FinancialAccountRef", "@referredType": "FinancialAccount"}
