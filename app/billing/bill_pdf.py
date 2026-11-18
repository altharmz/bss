"""The PDF of an issued bill (docs/decisions/bills.md, "PDF documents and email delivery").

Pure: `render(BillPdf)` returns the PDF bytes for data the caller loaded (the repository's
`bill_documents.load`). The layout is the issuer's header, the bill number, dates, account,
customer and billing address, then the charges (the detailed format) or the totals only
(summary), the taxes per category, what paid the bill so far and what is still due by when.
Money is printed from Decimal with the account currency, never through float. The font is
the vendored DejaVu Sans (app/billing/fonts, its licence beside it), so any name prints.
"""

import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONTS = Path(__file__).parent / "fonts"
FONT, BOLD = "DejaVuSans", "DejaVuSans-Bold"
DETAILED = "JSON detailed"


class RenderError(Exception):
    """The bill could not be rendered (the run fails the account: nothing is half-issued)."""


def bill_no(bill_id: Any) -> str:
    """The display number, like BA-xxxxxxxx for accounts: the first 8 hex digits."""
    return "BL-" + str(bill_id).replace("-", "")[:8].upper()


@dataclass(frozen=True)
class Charge:
    name: str
    start: date
    end: date  # exclusive, like every range
    amount: Decimal  # before tax
    total: Decimal  # tax included


@dataclass(frozen=True)
class TaxLine:
    category: str
    rate: Decimal
    amount: Decimal


@dataclass(frozen=True)
class Applied:
    """A payment or a credit note that paid (part of) the bill."""

    label: str
    amount: Decimal


@dataclass(frozen=True)
class BillPdf:
    bill_id: UUID
    issuer_name: str
    issuer_address: list[str]
    bill_date: date
    period_start: date
    period_end: date  # exclusive
    payment_due_date: date
    currency: str
    account_name: str
    account_id: UUID
    customer_name: str
    billing_address: list[str]
    format_name: str
    net: Decimal
    amount_due: Decimal
    remaining: Decimal
    taxes: list[TaxLine] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)  # empty for the summary format
    applied: list[Applied] = field(default_factory=list)


_registered = False


def _fonts() -> None:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont(FONT, str(FONTS / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(BOLD, str(FONTS / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=BOLD)
        _registered = True


def money(amount: Decimal, currency: str) -> str:
    if not isinstance(amount, Decimal):
        raise RenderError(f"money must be Decimal, not {type(amount).__name__}")
    return f"{amount:,.2f} {currency}"


def day(value: date) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def period(start: date, end: date) -> str:
    """A half-open range printed with its last day."""
    return f"{day(start)} - {day(end - timedelta(days=1))}"


def rate(value: Decimal) -> str:
    """A tax rate (0.0825) as a percentage (8.25%)."""
    return f"{(value * 100).normalize():f}%"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_BASE = ParagraphStyle("base", fontName=FONT, fontSize=9, leading=12)
_SMALL = ParagraphStyle("small", parent=_BASE, fontSize=8, leading=10, textColor=colors.grey)
_TITLE = ParagraphStyle("title", parent=_BASE, fontName=BOLD, fontSize=16, leading=20)
_HEAD = ParagraphStyle("head", parent=_BASE, fontName=BOLD, fontSize=11, leading=15)
_RULE = colors.HexColor("#999999")


def _lines(lines: list[str], style: ParagraphStyle = _BASE) -> Paragraph:
    return Paragraph("<br/>".join(_escape(line) for line in lines if line), style)


def _table(rows: list[list[Any]], widths: list[float], money_columns: int) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), FONT, 9),
                ("FONT", (0, 0), (-1, 0), BOLD, 9),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, _RULE),
                ("ALIGN", (-money_columns, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def render(bill: BillPdf) -> bytes:
    """The bill's PDF. Raises RenderError when it cannot be made."""
    try:
        return _render(bill)
    except RenderError:
        raise
    except Exception as error:  # noqa: BLE001 - any failure is the bill's render failure
        raise RenderError(f"bill {bill.bill_id}: {type(error).__name__}: {error}") from error


def _render(bill: BillPdf) -> bytes:
    _fonts()
    cur = bill.currency
    number = bill_no(bill.bill_id)
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Bill {number}",
        author=bill.issuer_name,
        subject=f"Bill {number} for {bill.account_name}",
        invariant=1,  # no timestamp or random id: the same data gives the same bytes
    )
    width = doc.width
    story: list[Any] = []

    header = Table(
        [
            [
                _lines([bill.issuer_name], _TITLE),
                Paragraph(f"<b>Bill {_escape(number)}</b>", _HEAD),
            ],
            [_lines(bill.issuer_address, _SMALL), ""],
        ],
        colWidths=[width * 0.6, width * 0.4],
    )
    header.setStyle(
        TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP")])
    )
    story += [header, Spacer(1, 8 * mm)]

    facts = [
        ["Bill number", number],
        ["Bill date", day(bill.bill_date)],
        ["Billing period", period(bill.period_start, bill.period_end)],
        ["Payment due", day(bill.payment_due_date)],
        ["Account", f"{bill.account_name} ({bill.account_id})"],
    ]
    bill_to = [bill.customer_name, *bill.billing_address]
    top = Table(
        [
            [
                [Paragraph("<b>Bill to</b>", _BASE), _lines(bill_to)],
                Table(
                    [
                        [Paragraph(_escape(k), _SMALL), Paragraph(_escape(v), _BASE)]
                        for k, v in facts
                    ],
                    colWidths=[28 * mm, width * 0.55 - 28 * mm],
                ),
            ]
        ],
        colWidths=[width * 0.45, width * 0.55],
    )
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [top, Spacer(1, 8 * mm)]

    if bill.format_name == DETAILED:
        story.append(Paragraph("Charges", _HEAD))
        rows: list[list[Any]] = [["Charge", "Period", "Amount", "With tax"]]
        rows += [
            [
                Paragraph(_escape(c.name), _BASE),
                period(c.start, c.end),
                money(c.amount, cur),
                money(c.total, cur),
            ]
            for c in bill.charges
        ]
        story += [
            _table(rows, [width * 0.4, width * 0.3, width * 0.15, width * 0.15], 2),
            Spacer(1, 5 * mm),
        ]

    story.append(Paragraph("Totals", _HEAD))
    tax_total = sum((t.amount for t in bill.taxes), Decimal("0"))
    totals: list[list[Any]] = [["", "Amount"], ["Charges before tax", money(bill.net, cur)]]
    totals += [[f"{t.category} ({rate(t.rate)})", money(t.amount, cur)] for t in bill.taxes]
    totals += [
        ["Total tax", money(tax_total, cur)],
        ["Bill total", money(bill.amount_due, cur)],
    ]
    story += [_table(totals, [width * 0.7, width * 0.3], 1), Spacer(1, 5 * mm)]

    story.append(Paragraph("Payments and credits", _HEAD))
    applied: list[list[Any]] = [["", "Amount"]]
    applied += [[Paragraph(_escape(a.label), _BASE), money(-a.amount, cur)] for a in bill.applied]
    if not bill.applied:
        applied.append(["None applied yet", money(Decimal("0.00"), cur)])
    story += [_table(applied, [width * 0.7, width * 0.3], 1), Spacer(1, 6 * mm)]

    if bill.amount_due < 0:
        due = f"Credit of {money(-bill.amount_due, cur)}: nothing to pay."
    elif bill.remaining == 0:
        due = f"Amount due: {money(bill.remaining, cur)}. Thank you, this bill is paid."
    else:
        due = (
            f"Amount due: {money(bill.remaining, cur)}, to be paid by {day(bill.payment_due_date)}."
        )
    story.append(Paragraph(f"<b>{_escape(due)}</b>", _HEAD))

    doc.build(story)
    return out.getvalue()
