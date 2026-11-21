import { formatCents, toCents } from '../format/money';

// The multi-line discount on a charge (docs/decisions/prepaid.md, "Multi-line discounts";
// issue #380): "Multi-line −$5.00", as the bill document and the TMF678 rate word it
// (app/billing/multi_line.py). The amount is the server's; nothing is computed here.

const SYMBOLS: Record<string, string> = { USD: '$', EUR: '€', GBP: '£' };

/** "Multi-line −$5.00" (or "Multi-line −5.00 CHF" for a currency without a symbol). */
export function multiLineLabel(amount: { value: number; unit: string }): string {
  const symbol = SYMBOLS[amount.unit];
  const off = formatCents(toCents(amount.value));
  return `Multi-line −${symbol === undefined ? `${off} ${amount.unit}` : `${symbol}${off}`}`;
}

/** The discount a TMF678 rate's description states ("… multi-line discount −$5.00
 * (productOfferingPrice <id>)"), as "Multi-line −$5.00"; null without one. */
export function rateDiscount(description: string | undefined | null): string | null {
  const found = /multi-line discount −(\S+(?: [A-Z]{3})?) \(productOfferingPrice/.exec(
    description ?? '',
  );
  return found === null ? null : `Multi-line −${found[1]}`;
}

/** The discount as a tag on a charge line. */
export function MultiLineDiscount({
  text,
  ui = 'ui.subscriber.multiLineDiscount',
}: {
  text: string;
  ui?: string;
}) {
  return (
    <span className="bss-secondary" data-ui={ui}>
      {text}
    </span>
  );
}
