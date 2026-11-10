"""Lists page without counts (#312, decided by the human 2026-11-10): no X-Total-Count,
X-Result-Count always, and `Link: <next page>; rel="next"` exactly when more rows follow."""

from urllib.parse import parse_qs, urlsplit

from tests import apidata


def link_of(response) -> dict[str, list[str]] | None:
    """The query of the Link rel=next target, None without one."""
    link = response.headers.get("Link")
    if link is None:
        return None
    assert link.endswith('; rel="next"'), link
    return parse_qs(urlsplit(link.split(">")[0].lstrip("<")).query)


def test_link_is_given_exactly_when_more_rows_follow(api):
    for name in ("One", "Two", "Three", "Four"):
        apidata.account(api, name=f"Paged {name}")
    url = apidata.ACCOUNT
    first = api.get(f"{url}?limit=2")
    assert first.headers["X-Result-Count"] == "2" and "X-Total-Count" not in first.headers
    assert link_of(first) == {"limit": ["2"], "offset": ["2"]}
    exact_last = api.get(f"{url}?limit=2&offset=2")  # the last page is exactly full
    assert len(exact_last.json()) == 2 and link_of(exact_last) is None
    short_last = api.get(f"{url}?limit=3&offset=3")
    assert len(short_last.json()) == 1 and link_of(short_last) is None
    beyond = api.get(f"{url}?offset=10")
    assert beyond.json() == [] and beyond.headers["X-Result-Count"] == "0"
    assert link_of(beyond) is None


def test_link_keeps_the_filters_and_moves_the_offset(api):
    for name in ("A", "B", "C"):
        apidata.account(api, name="Same name")
        apidata.account(api, name=f"Other {name}")
    page = api.get(f"{apidata.ACCOUNT}?name=Same%20name&limit=1&offset=1")
    assert link_of(page) == {"name": ["Same name"], "limit": ["1"], "offset": ["2"]}
    following = api.get(page.headers["Link"].split(">")[0].lstrip("<"))
    assert following.status_code == 200 and len(following.json()) == 1


def test_lists_that_returned_everything_now_page(api, db_conn):
    """An account's open products, a product's devices and port PINs, SIM batches and users
    took no limit before #312."""
    response = api.get("/internal/v1/users?limit=1")
    assert response.status_code == 200 and response.headers["X-Result-Count"] == "1"
    assert api.get("/internal/v1/users?limit=101").status_code == 400  # the maximum applies
    assert api.get("/internal/v1/sim/batch?limit=101").status_code == 400
