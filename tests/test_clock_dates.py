"""TMF637 product `creationDate` and TMF678 ABR `date` come from the injected clock, like
every other business instant (doc-findings #9) - not from the database's now(), which is
the audit column created_at. The FixedClock here is 2026, far from the wall clock the
database runs on, so a date taken from created_at cannot pass."""

from datetime import date, timedelta

from tests import apidata
from tests.apidata import RATE
from tests.test_billing_db import add, at, bill, delete, run_row
from tests.timeline import midnight, z


def test_product_creation_date_is_the_clock_of_its_order(api, db_conn):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    ordered_at = at(date(2026, 11, 20), hour=14)
    product = add(api, account, offering, date(2026, 12, 1), on=ordered_at)
    assert product["creationDate"] == z(ordered_at)
    # neither time passing nor the termination changes it
    delete(api, account, product, date(2027, 1, 1), on=at(date(2026, 12, 15)))
    assert api.get(product["href"]).json()["creationDate"] == z(ordered_at)
    # and it is not the row's insertion time
    created_at = db_conn.execute(
        "SELECT created_at FROM product WHERE id = %s", (product["id"],)
    ).fetchone()[0]
    assert z(created_at) != z(ordered_at)


def test_abr_date_is_the_clock_of_the_run_that_wrote_it(api, db_conn):
    offering = apidata.sellable_offering(api)
    account = apidata.account(api)
    product = add(api, account, offering, date(2026, 12, 1), on=at(date(2026, 11, 20)))
    bill(db_conn, account, date(2026, 12, 1))  # run_row: started 00:30 on the run date
    delete(api, account, product, date(2026, 12, 20), on=at(date(2026, 12, 10)))
    bill(db_conn, account, date(2027, 1, 1))  # the refund of Dec 20..31
    dec_run = run_row(db_conn, date(2026, 12, 1))

    def dates() -> dict[str, str]:
        rates = api.get(f"{RATE}?billingAccount.id={account['id']}").json()
        return {r["appliedBillingRateType"]: r["date"] for r in rates}

    expected = {
        "appliedBillingCharge": z(midnight(date(2026, 12, 1)) + timedelta(minutes=30)),
        "appliedBillingCredit": z(midnight(date(2027, 1, 1)) + timedelta(minutes=30)),
    }
    assert z(dec_run["started_at"]) == expected["appliedBillingCharge"]
    assert dates() == expected
    # a later attempt of the run (reopen, takeover) moves started_at; the ABRs it did not
    # write keep the instant they were written at
    db_conn.execute(
        "UPDATE billing_run SET started_at = started_at + interval '2 days' WHERE id = %s",
        (dec_run["id"],),
    )
    db_conn.commit()
    assert dates() == expected
