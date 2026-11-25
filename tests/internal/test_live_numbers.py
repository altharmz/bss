"""#456: the numbers the admin UI lists - each open product's live msisdn and imsi/SIM on
the subscriber card, and up to three live msisdns per account on the Subscribers list."""

from datetime import date

from app.repository import products, subscribers
from tests import factories
from tests.builders.resources.number_persistence import assignment, number

TODAY = date(2026, 3, 1)
JAN = date(2026, 1, 1)


def line(conn, acc, off, start=JAN):
    return factories.product(conn, acc["id"], off["id"], start)


def test_each_product_has_the_identities_live_today(db_conn):
    off = factories.offering(db_conn)
    acc = factories.account(db_conn)
    live, ended, changing, bare, sim, cancelled = (line(db_conn, acc, off) for _ in range(6))
    assignment(db_conn, number(db_conn, "12025550101"), live)
    assignment(db_conn, number(db_conn, "12025550102"), ended, end=TODAY)  # released today
    # a scheduled change: today's number until the 10th, the next one from then
    assignment(db_conn, number(db_conn, "12025550103"), changing, end=date(2026, 3, 10))
    assignment(db_conn, number(db_conn, "12025550104"), changing, start=date(2026, 3, 10))
    assignment(db_conn, number(db_conn, "12025550105"), sim)
    imsi = number(db_conn, "310260000000005", kind="imsi", iccid="8901260000000000005")
    assignment(db_conn, imsi, sim, kind="imsi")
    assignment(db_conn, number(db_conn, "12025550106"), cancelled, cancelled=True)

    found = products.live_identities(
        db_conn, [p["id"] for p in (live, ended, changing, bare, sim, cancelled)], TODAY
    )

    none = {"msisdn": None, "imsi": None, "iccid": None}
    assert found[live["id"]] == {**none, "msisdn": "12025550101"}
    assert found[ended["id"]] == none
    assert found[changing["id"]] == {**none, "msisdn": "12025550103"}
    assert found[bare["id"]] == none
    assert found[sim["id"]] == {
        "msisdn": "12025550105",
        "imsi": "310260000000005",
        "iccid": "8901260000000000005",
    }
    assert found[cancelled["id"]] == none
    assert products.live_identities(db_conn, [changing["id"]], date(2026, 3, 10)) == {
        changing["id"]: {**none, "msisdn": "12025550104"}
    }


def test_an_account_lists_three_live_numbers_oldest_line_first_and_counts_them(db_conn):
    off = factories.offering(db_conn)
    acc = factories.account(db_conn)
    other = factories.account(db_conn, name="Other")
    for day, value in ((4, "12025550204"), (1, "12025550201"), (3, "12025550203")):
        assignment(db_conn, number(db_conn, value), line(db_conn, acc, off, date(2026, 1, day)))
    assignment(db_conn, number(db_conn, "12025550202"), line(db_conn, acc, off, date(2026, 1, 2)))
    gone = line(db_conn, acc, off)  # an ended line's number is not listed
    assignment(db_conn, number(db_conn, "12025550200"), gone)
    db_conn.execute("UPDATE product SET status = 'terminated' WHERE id = %s", (gone["id"],))
    later = line(db_conn, acc, off)  # a number that starts later is not live yet
    assignment(db_conn, number(db_conn, "12025550205"), later, start=date(2026, 4, 1))
    sim = number(db_conn, "310260000000006", kind="imsi")  # an IMSI is not a number here
    assignment(db_conn, sim, line(db_conn, acc, off), kind="imsi")
    single = factories.account(db_conn, name="Single")
    assignment(db_conn, number(db_conn, "12025550301"), line(db_conn, single, off))

    found = subscribers.live_msisdns(db_conn, [acc["id"], other["id"], single["id"]], TODAY)

    assert found == {
        acc["id"]: (["12025550201", "12025550202", "12025550203"], 4),
        single["id"]: (["12025550301"], 1),
    }


def test_the_number_queries_read_by_index(db_conn):
    """A page reads its products' claims through indexes, never a scan of every claim."""
    off = factories.offering(db_conn)
    acc = factories.account(db_conn)
    prod = line(db_conn, acc, off)
    db_conn.execute("SET enable_seqscan = off")
    for query, params in (
        (subscribers.LIVE_MSISDNS, subscribers.live_msisdn_params([acc["id"]], TODAY)),
        (products.LIVE_IDENTITIES, {"ids": [prod["id"]], "on": TODAY}),
    ):
        plan = "\n".join(r[0] for r in db_conn.execute("EXPLAIN " + query, params))
        assert "Seq Scan" not in plan, plan
