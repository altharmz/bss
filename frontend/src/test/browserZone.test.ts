import { describe, expect, it } from 'vitest';

// `npm test` runs the whole suite twice: in the container's zone (UTC) and with the
// "browser" zone forced to America/New_York (TZ=America/New_York). Every request-body
// test then has to produce the same body in both: dates the UI sends are BILLING_TZ
// (from /internal/v1/context), never the browser's zone. This proves the second run
// really is in another zone - a TZ the runtime ignored would make it a repeat.

describe('the zone this suite runs in', () => {
  it('is the one TZ asks for', () => {
    const asked = process.env.TZ;
    const actual = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (asked === undefined) {
      expect(actual).toBe('UTC'); // the container's own zone
    } else {
      expect(actual).toBe(asked);
      // and Date really works in it: New York is behind UTC all year
      expect(new Date('2026-10-06T12:00:00Z').getTimezoneOffset()).toBeGreaterThan(0);
    }
  });
});
