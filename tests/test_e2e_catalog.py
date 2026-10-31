"""Browser catalog fixtures obey normal price-date rules without changing the API clock."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from tests.apidata import CATALOG, account
from tests.e2e_users import numbered_offering


@pytest.mark.smoke
def test_numbered_fixture_can_price_a_purchase_later_the_same_business_day(api):
    midnight = datetime.combine(api.clock.today(), time.min, ZoneInfo(settings.billing_tz))
    api.clock.set(midnight)
    fixture = numbered_offering(api)
    assert fixture["currency"] == settings.ui_currency
    offering = api.get(f"{CATALOG}/productOffering/{fixture['id']}").json()
    (reference,) = offering["productOfferingPrice"]
    price = api.get(reference["href"]).json()
    assert datetime.fromisoformat(price["validFor"]["startDateTime"]) == midnight

    # Catalog setup is finished; a later, real-time browser must still get a valid
    # same-day purchase quote. No date-rule waiver or backdated API request is used.
    api.clock.set(midnight + timedelta(hours=12))
    subscriber = account(api, ratingType="prepaid", currency=settings.ui_currency)
    response = api.get(
        f"/internal/v1/billingAccount/{subscriber['id']}/firstCharge"
        f"?productOffering.id={fixture['id']}&startDate={api.clock.today()}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["required"]["value"] == 1
