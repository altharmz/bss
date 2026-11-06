"""English templates per event and channel. `{name}` placeholders are filled from the
event's parameters (app/notify/events.py); every event renders for both channels."""

from typing import Any

# event -> channel -> (subject, body). SMS has no subject.
TEMPLATES: dict[str, dict[str, tuple[str | None, str]]] = {
    "low_balance": {
        "sms": (None, "Your balance is low: {balance}. Top up to keep your plan renewing."),
        "email": (
            "Your balance is low",
            "Hello {name},\n\nThe balance on your account is {balance}, below {threshold}."
            " Top up so your plan can renew on time.",
        ),
    },
    "renewal_done": {
        "sms": (None, "Your {plan} plan renewed. Paid through {date}."),
        "email": (
            "Your plan renewed",
            "Hello {name},\n\nYour {plan} plan has renewed and is paid through {date}.",
        ),
    },
    "renewal_failed": {
        "sms": (
            None,
            "We could not renew your {plan} plan: {shortfall} more is needed. Top up to renew.",
        ),
        "email": (
            "We could not renew your plan",
            "Hello {name},\n\nYour {plan} plan could not renew because the balance is short by"
            " {shortfall}. Top up and it will renew automatically.",
        ),
    },
    "plan_expiring": {
        "sms": (
            None,
            "Your {plan} plan ends in {days} day(s), on {date}. Keep your balance topped up.",
        ),
        "email": (
            "Your plan ends soon",
            "Hello {name},\n\nYour {plan} plan ends in {days} day(s), on {date}. Make sure your"
            " balance covers the renewal.",
        ),
    },
    "grace": {
        "sms": (None, "Your {plan} plan has ended. Top up by {date} to avoid suspension."),
        "email": (
            "Your plan has ended",
            "Hello {name},\n\nYour {plan} plan has ended and could not be renewed. Your service"
            " stays on until {date}; top up before then to avoid suspension.",
        ),
    },
    "suspended": {
        "sms": (None, "Your line {line} is suspended. Top up to restore service."),
        "email": (
            "Your line is suspended",
            "Hello {name},\n\nYour line {line} has been suspended because it is unpaid. Top up to"
            " restore service; the line is deactivated on {date} if nothing changes.",
        ),
    },
    "deactivated": {
        "sms": (None, "Your line {line} was deactivated for non-payment."),
        "email": (
            "Your line was deactivated",
            "Hello {name},\n\nYour line {line} has been deactivated because it stayed unpaid."
            " The number is held for a time before it is released.",
        ),
    },
    "usage_80": {
        "sms": (None, "You have used 80% of your {allowance} for this plan period on {line}."),
        "email": (
            "You have used 80% of your {allowance}",
            "Hello {name},\n\nLine {line} has used 80% of its {allowance} for this plan period.",
        ),
    },
    "usage_100": {
        "sms": (None, "You have used all of your {allowance} for this plan period on {line}."),
        "email": (
            "You have used all of your {allowance}",
            "Hello {name},\n\nLine {line} has used all of its {allowance} for this plan period.",
        ),
    },
    "card_declined": {
        "sms": (None, "Your card ending {last4} was declined ({reason}). Update your card."),
        "email": (
            "Your card was declined",
            "Hello {name},\n\nThe charge on your card ending {last4} was declined ({reason})."
            " Please update your payment method.",
        ),
    },
    "port_in_pending": {
        "sms": (None, "Your number {line} is being transferred to us."),
        "email": (
            "Your number transfer is under way",
            "Hello {name},\n\nThe transfer of {line} to us is in progress.",
        ),
    },
    "port_in_rejected": {
        "sms": (None, "The transfer of {line} was rejected: {reason}. Contact us to fix it."),
        "email": (
            "Your number transfer was rejected",
            "Hello {name},\n\nThe transfer of {line} was rejected by the other carrier: {reason}."
            " Contact us with the corrected details.",
        ),
    },
    "port_in_completed": {
        "sms": (None, "Your number {line} has been transferred to us. Welcome!"),
        "email": (
            "Your number has been transferred",
            "Hello {name},\n\nThe transfer of {line} is complete.",
        ),
    },
    "sim_swap": {
        "sms": (None, "The SIM on your line {line} was changed. Call us if this was not you."),
        "email": (
            "The SIM on your line was changed",
            "Hello {name},\n\nThe SIM on line {line} was changed. If this was not you, contact"
            " us at once.",
        ),
    },
}

EVENTS = tuple(TEMPLATES)


def render(event: str, channel: str, params: dict[str, Any]) -> tuple[str | None, str, str]:
    """(subject, body, template name) of an event on a channel."""
    subject, body = TEMPLATES[event][channel]
    return (
        None if subject is None else subject.format_map(params),
        body.format_map(params),
        f"{event}.{channel}",
    )
