import { afterEach, describe, expect, it, vi } from 'vitest';
import { json, overloadedError, stubFetch } from '../test/fetch';
import {
  amountSlot,
  ApiError,
  getAll,
  getJson,
  jsonWithAmounts,
  jsonWithSignedAmount,
  AMOUNT,
  newIdempotencyKey,
} from './client';

const PATH = '/tmf-api/accountManagement/v5/billingAccount';

afterEach(() => vi.restoreAllMocks());

describe('jsonWithSignedAmount', () => {
  it('writes a debit as a negative exact number token', () => {
    expect(jsonWithSignedAmount({ amount: { value: AMOUNT } }, '-12.50')).toBe(
      '{"amount":{"value":-12.50}}',
    );
    expect(jsonWithSignedAmount({ amount: AMOUNT }, '3.00')).toBe('{"amount":3.00}');
  });

  it('refuses anything but a canonical signed amount, and a body without exactly one slot', () => {
    expect(() => jsonWithSignedAmount({ v: AMOUNT }, '-12.5')).toThrow(
      new RangeError('not a canonical amount: -12.5'),
    );
    expect(() => jsonWithSignedAmount({ v: AMOUNT }, '+1.00')).toThrow(RangeError);
    expect(() => jsonWithSignedAmount({ v: 1 }, '-1.00')).toThrow(
      'the body must hold AMOUNT exactly once',
    );
    expect(() => jsonWithSignedAmount({ a: AMOUNT, b: AMOUNT }, '-1.00')).toThrow('exactly once');
  });
});

describe('jsonWithAmounts', () => {
  it('writes each amount into its own slot', () => {
    expect(jsonWithAmounts({ low: amountSlot(0), high: amountSlot(1) }, ['5.00', '1290.50'])).toBe(
      '{"low":5.00,"high":1290.50}',
    );
  });

  it('refuses a non-canonical amount and a slot missing or held twice', () => {
    expect(() => jsonWithAmounts({ a: amountSlot(0), b: amountSlot(1) }, ['1.00', '2'])).toThrow(
      new RangeError('not a canonical amount: 2'),
    );
    expect(() => jsonWithAmounts({ a: amountSlot(0) }, ['1.00', '2.00'])).toThrow(
      'the body must hold slot 1 exactly once',
    );
    expect(() => jsonWithAmounts({ a: amountSlot(0), b: amountSlot(0) }, ['1.00'])).toThrow(
      'the body must hold slot 0 exactly once',
    );
  });
});

describe('newIdempotencyKey', () => {
  it('uses crypto.randomUUID where the browser has it', () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('1f3a9c2b-0000-4000-8000-000000000001');
    expect(newIdempotencyKey()).toBe('1f3a9c2b-0000-4000-8000-000000000001');
  });

  it('falls back to random hex groups without it (an insecure context)', () => {
    vi.stubGlobal('crypto', {});
    const keys = new Set(Array.from({ length: 20 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(20);
    for (const key of keys) {
      // 1-255 visible ASCII, as the Idempotency-Key header allows
      expect(key).toMatch(/^[0-9a-f]{1,8}(-[0-9a-f]{1,8}){3}$/);
    }
  });
});

describe('getAll', () => {
  it('gives up on a list that keeps growing after 100 pages', async () => {
    const { calls } = stubFetch({ [`GET ${PATH}?*`]: () => json([{ id: 'a' }], { more: true }) });
    const error = await getAll(PATH).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ method: 'GET', path: PATH, status: 200 });
    expect(String((error as ApiError).message)).toContain('more than 10000 rows');
    expect(calls).toHaveLength(100);
    expect(calls.at(-1)).toBe(`GET ${PATH}?offset=99&limit=100`);
  });
});

describe('an overloaded read', () => {
  it('stops at once, without a resend, when aborted as its refusal arrives', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const controller = new AbortController();
    const fetchMock = vi.fn(async () => {
      controller.abort();
      return overloadedError();
    });
    vi.stubGlobal('fetch', fetchMock);
    const error = await getJson(PATH, controller.signal).catch((e: unknown) => e);
    expect(error).toMatchObject({ name: 'AbortError' });
    // no backoff timer was left running
    expect(vi.getTimerCount()).toBe(0);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
