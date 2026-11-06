"""The prepaid line lifecycle as dates (docs/decisions/prepaid.md, issue #188).

Everything derives from the first day a line is *unfunded* and the settings, so a change of
the settings moves every transition not yet taken. The schedule functions are pure;
`funded_state` and `view` read the line's funding.

    unfunded_since D ... D+grace-1   grace: service on, nothing sent
    D+grace                          suspension: network `suspend`
    D+grace+suspension               deactivation: the product ends, network `deactivate`;
                                     its number is then aged `number_aging_days`
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import psycopg

from app.billing import entitlement
from app.repository import funding
from app.repository._sql import Row

ENDED = frozenset({"terminated", "cancelled", "aborted"})


@dataclass(frozen=True)
class Settings:
    grace_days: int
    suspension_days: int
    number_aging_days: int
    payg_zero_balance_days: int


@dataclass(frozen=True)
class Schedule:
    suspend_on: date
    deactivate_on: date


def schedule(unfunded_since: date, settings: Settings) -> Schedule:
    suspend_on = unfunded_since + timedelta(days=settings.grace_days)
    return Schedule(suspend_on, suspend_on + timedelta(days=settings.suspension_days))


def state(unfunded_since: date | None, suspended: bool, today: date, settings: Settings) -> str:
    """`active` (funded), `grace` (unfunded, service on) or `suspended` (on the network)."""
    if suspended:
        return "suspended"
    if unfunded_since is None or today < unfunded_since:
        return "active"
    return "grace"


def next_transition(
    unfunded_since: date | None, suspended: bool, settings: Settings
) -> tuple[str, date] | None:
    """The next transition not yet taken: (`suspend` | `deactivate`, its date), if unfunded."""
    if unfunded_since is None:
        return None
    due = schedule(unfunded_since, settings)
    return ("deactivate", due.deactivate_on) if suspended else ("suspend", due.suspend_on)


def payg_unfunded_since(zero_since: date | None, settings: Settings) -> date | None:
    """A pay-as-you-go line is unfunded once its balance has been zero for the setting's days."""
    if zero_since is None:
        return None
    return zero_since + timedelta(days=settings.payg_zero_balance_days)


def is_payg(product: Row) -> bool:
    return bool(product["grantless"]) and product["parent_product_id"] is None


def funded_state(
    conn: psycopg.Connection,
    product: Row,
    account: Row,
    spendable: Decimal | None,
    settings: Settings,
    today: date,
) -> tuple[bool, date | None]:
    """(funded today, the first unfunded day). A plan is funded while a paid interval covers
    today (or starts later); a pay-as-you-go line while its balance is above zero, and it is
    unfunded `paygZeroBalanceDays` after the balance first reached zero."""
    if is_payg(product):
        since = payg_unfunded_since(account["balance_zero_since"], settings)
        return (spendable is None or spendable > 0), since
    grant = entitlement.latest_grant(conn, product["id"])
    if grant is None or product["grantless"]:
        return True, None  # not started yet, or an add-on: nothing to renew
    period = grant["period"]
    if period.lower > today or today < period.upper:
        return True, None
    return False, period.upper


def view(
    conn: psycopg.Connection, product: Row, account: Row, settings: Settings, today: date
) -> dict | None:
    """The lifecycle of a line as the internal API shows it, or None if it has none."""
    if account["rating_type"] != "prepaid" or (product["grantless"] and not is_payg(product)):
        return None
    if product["status"] in ENDED or product["termination_date"] is not None:
        return None
    if product["manually_suspended"] and not product["lifecycle_suspended"]:
        return None
    spendable = _spendable(conn, product, account)
    funded, since = funded_state(conn, product, account, spendable, settings, today)
    if funded:
        since = None
    suspended = bool(product["lifecycle_suspended"])
    current = state(since, suspended, today, settings)
    if funded and suspended:
        transition = None  # funded again: the next tick resumes it
    else:
        transition = next_transition(since, suspended, settings)
    return {
        "state": current,
        "nextTransition": None
        if transition is None
        else {"action": transition[0], "date": transition[1]},
    }


def _spendable(conn: psycopg.Connection, product: Row, account: Row) -> Decimal | None:
    if not is_payg(product):
        return None
    return funding.load(conn, account["id"]).spendable
