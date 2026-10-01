// Amounts, as the design formats them: thousands separator ",", decimal ".", always two
// decimals, negatives with the real minus sign U+2212 (never a hyphen).
//
// The API sends Money values as JSON numbers with at most 2 decimals (Decimal on the
// server). They become integer cents here, at the edge, and nothing in the UI does
// arithmetic on the binary floats; billing math stays on the backend.

export const MINUS = '−';
const LIMIT = 1e10; // numeric(12,2): at most 10 integer digits

/** A JSON amount as integer cents. Throws on anything that is not a 2-decimal amount. */
export function toCents(value: number): number {
  if (!Number.isFinite(value) || Math.abs(value) >= LIMIT) {
    throw new RangeError(`not an amount: ${value}`);
  }
  const cents = Math.round(value * 100);
  if (Math.abs(value * 100 - cents) > 1e-6) {
    throw new RangeError(`more than 2 decimals: ${value}`);
  }
  return cents === 0 ? 0 : cents; // never -0
}

function group(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/** 125040 -> "1,250.40"; -125040 -> "−1,250.40". */
export function formatCents(cents: number): string {
  if (!Number.isSafeInteger(cents)) throw new RangeError(`not integer cents: ${cents}`);
  const abs = Math.abs(cents);
  const text = `${group(String(Math.floor(abs / 100)))}.${String(abs % 100).padStart(2, '0')}`;
  return cents < 0 ? MINUS + text : text;
}

/** A JSON amount (e.g. Money.value) formatted. */
export function formatAmount(value: number): string {
  return formatCents(toCents(value));
}

/** Transaction amounts: "+100.00" for credits, "−300.00" for debits, "0.00" for zero. */
export function formatSigned(cents: number): string {
  return cents > 0 ? `+${formatCents(cents)}` : formatCents(cents);
}

/** Counts: 3500 -> "3,500". */
export function formatInt(value: number): string {
  if (!Number.isSafeInteger(value)) throw new RangeError(`not an integer: ${value}`);
  const text = group(String(Math.abs(value)));
  return value < 0 ? MINUS + text : text;
}

/**
 * An amount typed by an operator, as the canonical decimal string the API gets
 * ("1,290.5" -> "1290.50"), or null if it is not a non-negative amount with at most 2
 * decimals. Spaces are ignored; "," only as a thousands separator. The string goes into
 * the request body verbatim, so it never passes through a binary float.
 */
export function parseAmount(text: string): string | null {
  const match = /^(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?$/.exec(text.replace(/\s/g, ''));
  if (!match) return null;
  const integer = (match[1] ?? '').replace(/,/g, '').replace(/^0+(?=\d)/, '');
  if (integer.length > 10) return null;
  return `${integer}.${(match[2] ?? '').padEnd(2, '0')}`;
}
