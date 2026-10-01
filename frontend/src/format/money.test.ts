import { describe, expect, it } from 'vitest';
import {
  MINUS,
  formatAmount,
  formatCents,
  formatInt,
  formatSigned,
  parseAmount,
  toCents,
} from './money';

describe('toCents', () => {
  it.each([
    [1250.4, 125040],
    [164.52, 16452],
    [-116.13, -11613],
    [0.01, 1],
    [0, 0],
    [-0, 0],
    [9999999999.99, 999999999999],
  ])('%s -> %s', (value, cents) => {
    expect(Object.is(toCents(value), cents)).toBe(true);
  });

  it.each([1.005, 0.001, Number.NaN, Infinity, 1e10, -1e10])('rejects %s', (value) => {
    expect(() => toCents(value)).toThrow(RangeError);
  });
});

describe('formatCents', () => {
  it.each([
    [125040, '1,250.40'],
    [100, '1.00'],
    [5, '0.05'],
    [0, '0.00'],
    [123456789012, '1,234,567,890.12'],
    [-11613, `${MINUS}116.13`],
    [-125040, `${MINUS}1,250.40`],
  ])('%s -> %s', (cents, text) => {
    expect(formatCents(cents)).toBe(text);
  });

  it('uses U+2212, never a hyphen', () => {
    const text = formatCents(-1);
    expect(text.codePointAt(0)).toBe(0x2212);
    expect(text).not.toContain('-');
  });

  it('refuses non-integer cents', () => {
    expect(() => formatCents(1.5)).toThrow(RangeError);
  });
});

describe('formatAmount, formatSigned, formatInt', () => {
  it('formats JSON amounts', () => {
    expect(formatAmount(-164.52)).toBe(`${MINUS}164.52`);
    expect(formatAmount(300)).toBe('300.00');
  });

  it('signs credits and debits', () => {
    expect(formatSigned(10000)).toBe('+100.00');
    expect(formatSigned(-30000)).toBe(`${MINUS}300.00`);
    expect(formatSigned(0)).toBe('0.00');
  });

  it('groups counts', () => {
    expect(formatInt(3500)).toBe('3,500');
    expect(formatInt(0)).toBe('0');
    expect(formatInt(-12)).toBe(`${MINUS}12`);
  });
});

describe('parseAmount', () => {
  it.each([
    ['1290', '1290.00'],
    ['1,290.5', '1290.50'],
    ['1 290.50', '1290.50'],
    ['0.01', '0.01'],
    ['007.10', '7.10'],
    ['9999999999.99', '9999999999.99'],
  ])('%s -> %s', (text, parsed) => {
    expect(parseAmount(text)).toBe(parsed);
  });

  it.each(['', '-1', '1.005', '1,29', '12,34.00', 'abc', '1.2.3', '1e3', '12345678901'])(
    'rejects %j',
    (text) => {
      expect(parseAmount(text)).toBeNull();
    },
  );
});
