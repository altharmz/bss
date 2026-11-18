"""The network partner's wholesale invoice format (docs/decisions/wholesale.md).

Our own contract until the real partner format is mapped (#203): a CSV file, UTF-8, with the
header `msisdn,iccid,period_start,period_end,charge_type,quantity,unit,amount,currency`.
`parse_invoice` is the only place that knows it; the reconciliation sees `InvoiceLines`.

- `msisdn` E.164 digits without plus, `iccid` 18 to 22 digits; at least one of them.
- `period_start` and `period_end` are dates (YYYY-MM-DD), the end exclusive (half-open).
- `charge_type` is `line` (the monthly access charge), `data`, `voice`, `sms` or `other`;
  `unit` is `MB` (data), `minute` (voice), `message` (sms) or empty (line, other).
- `quantity` a non-negative decimal (empty = 0), `amount` a decimal string with at most two
  decimals, read as Decimal: never a float.
- One currency per file.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

HEADER = [
    "msisdn",
    "iccid",
    "period_start",
    "period_end",
    "charge_type",
    "quantity",
    "unit",
    "amount",
    "currency",
]
CHARGE_TYPES = ("line", "data", "voice", "sms", "other")
UNIT_OF = {"data": "MB", "voice": "minute", "sms": "message"}
MAX_LINES = 200_000
MSISDN = re.compile(r"^[1-9][0-9]{7,14}$")
ICCID = re.compile(r"^[0-9]{18,22}$")
DECIMAL = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
CURRENCY = re.compile(r"^[A-Z]{3}$")


class InvoiceError(ValueError):
    """What is wrong with the file, naming the row (1 = the first row after the header) and
    the column."""


class TooLarge(InvoiceError):
    """More rows than one synchronous reconciliation takes."""


@dataclass(frozen=True)
class InvoiceLine:
    line_number: int
    msisdn: str | None
    iccid: str | None
    period_start: date
    period_end: date
    charge_type: str
    quantity: Decimal
    unit: str | None
    amount: Decimal


@dataclass(frozen=True)
class InvoiceLines:
    currency: str
    lines: list[InvoiceLine]

    @property
    def period(self) -> tuple[date, date]:
        return (
            min(line.period_start for line in self.lines),
            max(line.period_end for line in self.lines),
        )

    @property
    def total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal("0.00"))


def _decimal(raw: str, where: str, *, places: int | None, negative: bool) -> Decimal:
    if not DECIMAL.match(raw):
        raise InvoiceError(f"{where}: {raw!r} is not a decimal number")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise InvoiceError(f"{where}: {raw!r} is not a decimal number") from None
    if not negative and value < 0:
        raise InvoiceError(f"{where}: must not be negative")
    if places is not None and -value.as_tuple().exponent > places:
        raise InvoiceError(f"{where}: at most {places} decimal places")
    return value


def _date(raw: str, where: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    raise InvoiceError(f"{where}: {raw!r} is not a date (YYYY-MM-DD)")


def parse_invoice(content: bytes) -> InvoiceLines:
    """The lines of a wholesale invoice file; InvoiceError says the first problem."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvoiceError("the file is not UTF-8 text") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None or [h.strip() for h in header] != HEADER:
        raise InvoiceError("header: must be exactly " + ",".join(HEADER))
    lines: list[InvoiceLine] = []
    currency = None
    for number, row in enumerate(reader, start=1):
        if not row or all(not cell.strip() for cell in row):
            continue  # a blank line (a trailing newline) is not a row
        if len(lines) >= MAX_LINES:
            raise TooLarge(f"more than {MAX_LINES} rows: split the invoice")
        if len(row) != len(HEADER):
            raise InvoiceError(f"row {number}: {len(row)} columns, expected {len(HEADER)}")
        cells = dict(zip(HEADER, (cell.strip() for cell in row), strict=True))

        def at(column: str, n: int = number) -> str:
            return f"row {n}, column {column}"

        msisdn, iccid = cells["msisdn"] or None, cells["iccid"] or None
        if msisdn is None and iccid is None:
            raise InvoiceError(f"{at('msisdn')}: msisdn or iccid is required")
        if msisdn is not None and not MSISDN.match(msisdn):
            raise InvoiceError(f"{at('msisdn')}: {msisdn!r} is not E.164 digits")
        if iccid is not None and not ICCID.match(iccid):
            raise InvoiceError(f"{at('iccid')}: {iccid!r} is not an ICCID (18 to 22 digits)")
        start = _date(cells["period_start"], at("period_start"))
        end = _date(cells["period_end"], at("period_end"))
        if end <= start:
            raise InvoiceError(f"{at('period_end')}: must be after period_start (exclusive end)")
        charge_type = cells["charge_type"]
        if charge_type not in CHARGE_TYPES:
            raise InvoiceError(f"{at('charge_type')}: must be one of {', '.join(CHARGE_TYPES)}")
        unit = cells["unit"] or None
        if unit != UNIT_OF.get(charge_type):
            expected = UNIT_OF.get(charge_type)
            raise InvoiceError(
                f"{at('unit')}: must be {expected!r} for {charge_type}"
                if expected
                else f"{at('unit')}: must be empty for {charge_type}"
            )
        quantity = (
            _decimal(cells["quantity"], at("quantity"), places=3, negative=False)
            if cells["quantity"]
            else Decimal(0)
        )
        amount = _decimal(cells["amount"], at("amount"), places=2, negative=True)
        if not CURRENCY.match(cells["currency"]):
            raise InvoiceError(f"{at('currency')}: {cells['currency']!r} is not an ISO 4217 code")
        if currency is not None and cells["currency"] != currency:
            raise InvoiceError(f"{at('currency')}: one currency per invoice ({currency})")
        currency = cells["currency"]
        lines.append(
            InvoiceLine(number, msisdn, iccid, start, end, charge_type, quantity, unit, amount)
        )
    if not lines or currency is None:
        raise InvoiceError("the invoice has no rows")
    return InvoiceLines(currency, lines)
