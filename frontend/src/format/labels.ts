// Human labels for API codes. Screens show the label and keep the raw code in `title`.

const IRREGULAR: Record<string, string> = { currency: 'currencies' };

/** "1 product", "2 products": the noun is singular; an irregular plural is looked up. */
export function plural(n: number, noun: string): string {
  if (n === 1) return `1 ${noun}`;
  const many =
    IRREGULAR[noun] ??
    (noun.endsWith('y') && !/[aeiou]y$/.test(noun) ? `${noun.slice(0, -1)}ies` : `${noun}s`);
  return `${n} ${many}`;
}

const PAYMENT_METHODS: Record<string, string> = {
  bankTransfer: 'Bank transfer',
  'bank transfer': 'Bank transfer',
  card: 'Card',
  cash: 'Cash',
  other: 'Other',
};

const RUN_TRIGGERS: Record<string, string> = {
  onCycle: 'Scheduled',
  offCycle: 'Off-cycle',
};

const EVENT_TYPES: Record<string, string> = {
  CustomerBill: 'Bill',
  AppliedCustomerBillingRate: 'Charge',
  'TMF654 balanceActionHistory': 'Balance action history',
  balanceActionHistory: 'Balance action history',
};

function lookup(table: Record<string, string>, code: string): string {
  return Object.hasOwn(table, code) ? (table[code] ?? code) : code;
}

export const paymentMethodLabel = (code: string) => lookup(PAYMENT_METHODS, code);
export const runTriggerLabel = (code: string) => lookup(RUN_TRIGGERS, code);

/** Event type names: "CustomerBillCreateEvent" reads "Bill create event". */
export function eventTypeLabel(code: string): string {
  if (Object.hasOwn(EVENT_TYPES, code)) return EVENT_TYPES[code] ?? code;
  const m = /^(CustomerBill|AppliedCustomerBillingRate)?(.*?)Event$/.exec(code);
  if (!m) return code;
  const subject = m[1] ? lookup(EVENT_TYPES, m[1]) : '';
  const words = (m[2] ?? '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
  const text = `${subject} ${words}`.trim();
  return text.charAt(0).toUpperCase() + text.slice(1) + ' event';
}

/** "5 min ago", "16 h ago", "3 days ago": the age of an instant at `now` (both ISO). */
export function agoLabel(instant: string, now: string): string {
  const minutes = Math.max(0, Math.floor((Date.parse(now) - Date.parse(instant)) / 60_000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${plural(Math.floor(hours / 24), 'day')} ago`;
}

/** The payment method choices of Record payment, Record refund and the prepaid top-up. */
export const PAYMENT_METHOD_OPTIONS = [
  { key: 'bankTransfer', label: 'Bank transfer' },
  { key: 'card', label: 'Card' },
  { key: 'cash', label: 'Cash' },
  { key: 'other', label: 'Other' },
];

const BILLING_STATUS_KEYS: Record<string, string> = {
  date: 'Business date',
  status: 'Status',
  ageSeconds: 'Last run',
};

/** The /billing-status keys as people read them; an unknown key stays as it is. */
export const billingStatusLabel = (key: string) => lookup(BILLING_STATUS_KEYS, key);

/** A /billing-status value: the age as "5 min ago"-style text, status codes capitalised. */
export function billingStatusValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (key === 'ageSeconds' && typeof value === 'number') {
    const minutes = Math.floor(value / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours} h ago`;
    return `${plural(Math.floor(hours / 24), 'day')} ago`;
  }
  if (key === 'status' && typeof value === 'string') {
    return value.charAt(0).toUpperCase() + value.slice(1);
  }
  return String(value);
}
