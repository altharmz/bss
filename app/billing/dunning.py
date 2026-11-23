"""Postpaid dunning as dates (docs/decisions/dunning.md, issue #385).

Everything derives from O, the first business date an account has an open, non-held bill
past its payment due date (the oldest such due date + 1), and the settings, so a change of
the settings moves every step not yet taken. Pure: the worker (app/worker/dunning.py) and
the internal views read the facts and ask this module what is due.

    O + reminder1Days    first reminder (email and SMS)
    O + reminder2Days    second reminder
    O + suspendDays      every active product of the account suspended through an order

An account owing less than `minimumOverdue` past due is not in dunning. Only the latest due
step is taken, and none once a later one was taken: a worker that was down, or a settings
change, never sends a reminder after the suspension or two reminders on one day.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

STEPS = ("reminder1", "reminder2", "suspend")


@dataclass(frozen=True)
class Settings:
    reminder1_days: int
    reminder2_days: int
    suspend_days: int
    minimum_overdue: Decimal


def overdue_since(due_dates: Iterable[date]) -> date | None:
    """O for the due dates of the overdue bills: the day after the oldest; None if none."""
    oldest = min(due_dates, default=None)
    return None if oldest is None else oldest + timedelta(days=1)


def in_dunning(o: date | None, amount: Decimal, settings: Settings) -> bool:
    return o is not None and amount > 0 and amount >= settings.minimum_overdue


def schedule(o: date, settings: Settings) -> dict[str, date]:
    """The date of each step for O, in step order."""
    days = (settings.reminder1_days, settings.reminder2_days, settings.suspend_days)
    return {step: o + timedelta(days=n) for step, n in zip(STEPS, days, strict=True)}


def due_step(o: date, settings: Settings, today: date, taken: Iterable[str]) -> str | None:
    """The step to take today: the latest one whose date has come, unless it or a later step
    was taken already."""
    done = set(taken)
    due = [step for step, on in schedule(o, settings).items() if on <= today]
    if not due:
        return None
    latest = due[-1]
    if any(step in done for step in STEPS[STEPS.index(latest) :]):
        return None
    return latest


def next_step(
    o: date, settings: Settings, today: date, taken: Iterable[str]
) -> tuple[str, date] | None:
    """The step the worker takes next and its date: the one due now (dated today or earlier,
    while the worker has not run yet), else the first later one; None once the suspension is
    taken."""
    done = set(taken)
    later = [step for step in STEPS if step in done]
    pending = STEPS[STEPS.index(later[-1]) + 1 :] if later else STEPS
    if not pending:
        return None
    dates = schedule(o, settings)
    due = due_step(o, settings, today, done)
    step = due if due is not None else next(s for s in pending if dates[s] > today)
    return step, dates[step]
