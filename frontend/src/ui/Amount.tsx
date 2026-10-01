import { formatCents, formatSigned } from '../format/money';

// An amount as the design shows it: mono tabular figures, negatives in red with U+2212,
// the currency code after it in 11px faint (README "Numbers").

export function Amount({
  cents,
  currency,
  signed = false,
  chip = false,
  strong = false,
}: {
  cents: number;
  currency?: string;
  signed?: boolean; // "+" for credits, green; "−" for debits, red (transactions)
  chip?: boolean; // negative balances in the subscribers list: red on a light red chip
  strong?: boolean;
}) {
  const negative = cents < 0;
  const classes = ['bss-amount'];
  if (negative) classes.push('negative');
  if (signed && cents > 0) classes.push('positive');
  if (chip && negative) classes.push('chip');
  if (strong || (chip && negative)) classes.push('strong');
  return (
    <span className={classes.join(' ')}>
      <span className="value">{signed ? formatSigned(cents) : formatCents(cents)}</span>
      {currency !== undefined && <span className="currency">{currency}</span>}
    </span>
  );
}
