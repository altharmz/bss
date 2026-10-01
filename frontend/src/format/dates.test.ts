import { describe, expect, it } from 'vitest';
import { addDays, businessDate, clockTime, isIsoDate } from './dates';

const MSK = 'Europe/Moscow';

describe('businessDate', () => {
  it('is the date in the billing time zone, not UTC', () => {
    // a TMF date: midnight Moscow, sent as UTC
    expect(businessDate('2026-11-30T21:00:00Z', MSK)).toBe('2026-12-01');
    expect(businessDate('2026-11-30T21:00:00Z', 'UTC')).toBe('2026-11-30');
    expect(businessDate('2026-10-01T23:30:00Z', MSK)).toBe('2026-10-02');
  });

  it('accepts offsets', () => {
    expect(businessDate('2026-10-01T23:30:00+03:00', MSK)).toBe('2026-10-01');
  });

  it('rejects garbage', () => {
    expect(() => businessDate('yesterday', MSK)).toThrow(RangeError);
  });
});

describe('clockTime', () => {
  it('is 24-hour HH:MM:SS in the zone', () => {
    expect(clockTime('2026-10-01T21:30:05Z', MSK)).toBe('00:30:05');
    expect(clockTime('2026-10-01T09:00:00Z', MSK)).toBe('12:00:00');
  });
});

describe('isIsoDate and addDays', () => {
  it.each([
    ['2026-02-28', true],
    ['2028-02-29', true],
    ['2027-02-29', false],
    ['2026-13-01', false],
    ['2026-1-01', false],
    ['20261001', false],
  ])('isIsoDate(%s) = %s', (text, ok) => {
    expect(isIsoDate(text)).toBe(ok);
  });

  it('adds calendar days across months and years', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2028-03-01', -1)).toBe('2028-02-29');
    expect(() => addDays('2026-02-30', 1)).toThrow(RangeError);
  });
});
