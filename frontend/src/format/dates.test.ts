import { describe, expect, it } from 'vitest';
import {
  addDays,
  businessDate,
  clockTime,
  isIsoDate,
  lastDay,
  nextCycleDate,
  utcLabel,
  zonedDateTime,
} from './dates';

describe('zonedDateTime', () => {
  it.each([
    ['2026-10-06', '12:00', 'Europe/Moscow', '2026-10-06T12:00:00+03:00'],
    ['2026-10-02', '00:00', 'Europe/Moscow', '2026-10-02T00:00:00+03:00'],
    // a DST zone: the offset of that date, not of today
    ['2026-07-01', '00:00', 'America/New_York', '2026-07-01T00:00:00-04:00'],
    ['2026-12-01', '00:00', 'America/New_York', '2026-12-01T00:00:00-05:00'],
    // the change days: midnight is before the switch, noon after it
    ['2026-03-08', '00:00', 'America/New_York', '2026-03-08T00:00:00-05:00'],
    ['2026-03-08', '12:00', 'America/New_York', '2026-03-08T12:00:00-04:00'],
    ['2026-11-01', '00:00', 'America/New_York', '2026-11-01T00:00:00-04:00'],
    ['2026-11-01', '12:00', 'America/New_York', '2026-11-01T12:00:00-05:00'],
    ['2026-10-06', '00:00', 'UTC', '2026-10-06T00:00:00+00:00'],
    ['2026-10-06', '00:00', 'Asia/Kolkata', '2026-10-06T00:00:00+05:30'],
  ])('%s %s in %s -> %s', (day, time, zone, expected) => {
    expect(zonedDateTime(day, time, zone)).toBe(expected);
  });

  it('means that very local time in the zone', () => {
    const text = zonedDateTime('2026-03-08', '12:00', 'America/New_York');
    expect(clockTime(text, 'America/New_York')).toBe('12:00:00');
    expect(businessDate(text, 'America/New_York')).toBe('2026-03-08');
  });
});

describe('nextCycleDate', () => {
  it.each([
    ['2026-10-01', 1, '2026-11-01'], // today is the cycle day: the next one
    ['2026-10-01', 5, '2026-10-05'],
    ['2026-10-15', 5, '2026-11-05'],
    ['2026-12-20', 15, '2027-01-15'],
    ['2028-02-29', 28, '2028-03-28'],
    ['2026-01-31', 28, '2026-02-28'],
  ])('after %s, day %s -> %s', (today, day, next) => {
    expect(nextCycleDate(today, day)).toBe(next);
  });

  it('refuses days outside 1..28', () => {
    expect(() => nextCycleDate('2026-10-01', 29)).toThrow(RangeError);
    expect(() => nextCycleDate('2026-10-01', 0)).toThrow(RangeError);
  });

  it('shows the last included day of a half-open range', () => {
    expect(lastDay('2027-01-01')).toBe('2026-12-31');
  });
});

describe('utcLabel', () => {
  it.each([
    ['+03:00', 'UTC+3'],
    ['+05:30', 'UTC+5:30'],
    ['-04:00', 'UTC−4'],
    ['+00:00', 'UTC'],
    ['-00:00', 'UTC'],
    ['+10:00', 'UTC+10'],
  ])('%s -> %s', (offset, label) => {
    expect(utcLabel(offset)).toBe(label);
  });

  it('rejects garbage', () => {
    expect(() => utcLabel('+3')).toThrow(RangeError);
  });
});

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
