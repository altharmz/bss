import { formatCents, formatSigned } from '../format/money';

/** Ledger figures: mono, tabular, U+2212 negatives; a plus only for movements. */
export function Amount({
  cents,
  currency,
  signed = false,
}: {
  cents: number;
  currency?: string;
  signed?: boolean;
}) {
  return (
    <span className={`bss-amount${cents < 0 ? ' negative' : ''}`}>
      <span className="value">{signed ? formatSigned(cents) : formatCents(cents)}</span>
      {currency !== undefined && <span className="currency">{currency}</span>}
    </span>
  );
}
