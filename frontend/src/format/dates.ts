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

/** The UTC offset of `timeZone` at an instant, in minutes east of UTC. From Intl, so it
 * never depends on the browser's own zone. */
function offsetMinutes(instant: number, timeZone: string): number {
  const name = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'longOffset' })
    .formatToParts(new Date(instant))
    .find((part) => part.type === 'timeZoneName')?.value;
  if (name === 'GMT') return 0;
  const match = name ? /^GMT([+-])(\d{2}):(\d{2})$/.exec(name) : null;
  if (!match) throw new RangeError(`no UTC offset for ${timeZone}: ${String(name)}`);
  const [, sign, hh, mm] = match as unknown as [string, string, string, string];
  return (sign === '-' ? -1 : 1) * (Number(hh) * 60 + Number(mm));
}

function formatOffset(minutes: number): string {
  const abs = Math.abs(minutes);
  const hh = String(Math.floor(abs / 60)).padStart(2, '0');
  const mm = String(abs % 60).padStart(2, '0');
  return `${minutes < 0 ? '-' : '+'}${hh}:${mm}`;
}

/**
 * The wall-clock time `hhmm` ("00:00", "12:00") of business date `day` in `timeZone`
 * (BILLING_TZ from /internal/v1/context), as an ISO date-time with that zone's offset ON
 * THAT DATE - a DST zone has two offsets a year. Never the browser's zone.
 * "2026-07-01", "00:00", "America/New_York" -> "2026-07-01T00:00:00-04:00".
 */
export function zonedDateTime(day: string, hhmm: string, timeZone: string): string {
  if (!isIsoDate(day) || !/^\d{2}:\d{2}$/.test(hhmm)) throw new RangeError(`${day} ${hhmm}`);
  const [y, m, d] = day.split('-').map(Number) as [number, number, number];
  const [hh, mm] = hhmm.split(':').map(Number) as [number, number];
  const asIfUtc = Date.UTC(y, m - 1, d, hh, mm);
  // the offset at the guessed instant, then at the instant it implies (DST edges)
  const guess = offsetMinutes(asIfUtc, timeZone);
  const offset = offsetMinutes(asIfUtc - guess * 60_000, timeZone);
  return `${day}T${hhmm}:00${formatOffset(offset)}`;
}

/** The top bar's zone label from /internal/v1/context's utcOffset: "+03:00" -> "UTC+3",
 * "+05:30" -> "UTC+5:30", "-04:00" -> "UTC−4" (U+2212), "+00:00" -> "UTC". */
export function utcLabel(offset: string): string {
  const match = /^([+-])(\d{2}):(\d{2})$/.exec(offset);
  if (!match) throw new RangeError(`not a UTC offset: ${offset}`);
  const [, sign, hh, mm] = match as unknown as [string, string, string, string];
  const hours = Number(hh);
  if (hours === 0 && mm === '00') return 'UTC';
  return `UTC${sign === '-' ? '−' : '+'}${hours}${mm === '00' ? '' : `:${mm}`}`;
}

/** The next cycle day strictly after `today`: the start of the account's next billing
 * period (cycle days are 1..28, so the day exists in every month). Date only - every
 * amount comes from the backend. */
export function nextCycleDate(today: string, cycleDay: number): string {
  if (!isIsoDate(today) || !Number.isInteger(cycleDay) || cycleDay < 1 || cycleDay > 28) {
    throw new RangeError(`bad input: ${today}, day ${cycleDay}`);
  }
  const [y, m, d] = today.split('-').map(Number) as [number, number, number];
  const month = d < cycleDay ? m - 1 : m; // 0-based: this month if the day is still ahead
  return new Date(Date.UTC(y, month, cycleDay)).toISOString().slice(0, 10);
}

/** Our ranges are half-open [start, end); the design shows the last included day. */
export function lastDay(endExclusive: string): string {
  return addDays(endExclusive, -1);
}

/** Calendar arithmetic on YYYY-MM-DD, independent of any time zone. */
export function addDays(day: string, days: number): string {
  if (!isIsoDate(day)) throw new RangeError(`not a date: ${day}`);
  const [y, m, d] = day.split('-').map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}
