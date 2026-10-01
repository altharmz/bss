// Dates as the design shows them: ISO YYYY-MM-DD, times HH:MM:SS. Business dates are the
// calendar date in BILLING_TZ, whose name comes from GET /internal/v1/context - never the
// browser's own time zone. No date library: Intl does the zone conversion.

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A valid calendar date in YYYY-MM-DD. */
export function isIsoDate(text: string): boolean {
  const match = ISO_DATE.exec(text);
  if (!match) return false;
  const [, y, m, d] = match.map(Number) as [number, number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

function parts(instant: string, timeZone: string, options: Intl.DateTimeFormatOptions) {
  const date = new Date(instant);
  if (Number.isNaN(date.getTime())) throw new RangeError(`not a date-time: ${instant}`);
  const found: Record<string, string> = {};
  for (const part of new Intl.DateTimeFormat('en-GB', { timeZone, ...options }).formatToParts(
    date,
  )) {
    found[part.type] = part.value;
  }
  return found;
}

/** The calendar date of an instant in `timeZone`: TMF dates (midnight BILLING_TZ, sent as
 * UTC) become the business date they mean. "2026-11-30T21:00:00Z" -> "2026-12-01" in
 * Europe/Moscow. */
export function businessDate(instant: string, timeZone: string): string {
  const p = parts(instant, timeZone, { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}-${p.month}-${p.day}`;
}

/** The wall-clock time of an instant in `timeZone`, HH:MM:SS (24 h). */
export function clockTime(instant: string, timeZone: string): string {
  const p = parts(instant, timeZone, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  });
  return `${p.hour}:${p.minute}:${p.second}`;
}

/** Calendar arithmetic on YYYY-MM-DD, independent of any time zone. */
export function addDays(day: string, days: number): string {
  if (!isIsoDate(day)) throw new RangeError(`not a date: ${day}`);
  const [y, m, d] = day.split('-').map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}
