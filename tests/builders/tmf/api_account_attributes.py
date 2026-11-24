"""Builders other test modules share, from tests/tmf/test_api_account_attributes.py (#423)."""

CONTACT = [
    {
        "@type": "Contact",
        "contactName": "Anna Cristal",
        "contactType": "primary",
        "partyRoleType": "Publisher",
        "validFor": {"startDateTime": "2018-06-11T03:00:00+03:00"},
        "contactMedium": [
            {
                "@type": "GeographicAddressContactMedium",
                "contactType": "postal",
                "city": "Paris",
                "country": "France",
                "postCode": "75014",
                "street1": "15 Rue des Canards",
            }
        ],
    }
]
