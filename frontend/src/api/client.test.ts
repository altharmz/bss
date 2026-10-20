import { describe, expect, it, vi } from 'vitest';
import { json, stubFetch, tmfError } from '../test/fetch';
import { AMOUNT, ApiError, getAll, getJson, getPage, jsonWithAmount } from './client';

const PATH = '/tmf-api/accountManagement/v5/billingAccount';

describe('errors', () => {
  it('notifies the session gate when a protected request returns 401', async () => {
    const expired = vi.fn();
    window.addEventListener('bss:unauthorized', expired);
    try {
      stubFetch({ [`GET ${PATH}`]: () => tmfError(401, 'Unauthorized') });
      await expect(getJson(PATH)).rejects.toThrow('401');
      expect(expired).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener('bss:unauthorized', expired);
    }
  });
  it('carries the call and the TMF reason', async () => {
    stubFetch({ [`GET ${PATH}`]: () => tmfError(503, 'Service Unavailable') });
    const error = await getJson(PATH).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe(`GET ${PATH} → 503 Service Unavailable`);
  });

  it('falls back to the status phrase when the body is not a TMF error (HTTP/2 has none)', async () => {
    stubFetch({ [`GET ${PATH}`]: () => new Response('<html>bad gateway</html>', { status: 502 }) });
    await expect(getJson(PATH)).rejects.toThrow(`GET ${PATH} → 502 Bad Gateway`);
  });

  it('reports a network failure', async () => {
    stubFetch({
      [`GET ${PATH}`]: () => {
        throw new TypeError('Failed to fetch');
      },
    });
    await expect(getJson(PATH)).rejects.toThrow(`GET ${PATH} → Network error`);
  });
});

describe('jsonWithAmount', () => {
  it('writes the amount as an exact number token', () => {
    const text = jsonWithAmount({ price: { unit: 'USD', value: AMOUNT } }, '1290.50');
    expect(text).toBe('{"price":{"unit":"USD","value":1290.50}}');
    // JSON.stringify of the float would have lost the trailing zero (and, in general, exactness)
    expect(JSON.stringify({ value: 1290.5 })).toBe('{"value":1290.5}');
  });

  it('refuses anything but a canonical amount, and a body without exactly one slot', () => {
    expect(() => jsonWithAmount({ v: AMOUNT }, '1290.5')).toThrow(RangeError);
    expect(() => jsonWithAmount({ v: AMOUNT }, '-1.00')).toThrow(RangeError);
    expect(() => jsonWithAmount({ v: 1 }, '1.00')).toThrow('exactly once');
    expect(() => jsonWithAmount({ a: AMOUNT, b: AMOUNT }, '1.00')).toThrow('exactly once');
  });
});

describe('pages', () => {
  it('reads X-Total-Count', async () => {
    stubFetch({ [`GET ${PATH}?limit=1`]: () => json([{ id: 'a' }], { total: 42 }) });
    expect(await getPage(`${PATH}?limit=1`)).toEqual({ items: [{ id: 'a' }], total: 42 });
  });

  it('refuses a list without X-Total-Count', async () => {
    stubFetch({ [`GET ${PATH}`]: () => json([]) });
    await expect(getPage(PATH)).rejects.toThrow('missing X-Total-Count');
  });

  it('getAll walks the pages 100 at a time', async () => {
    const rows = Array.from({ length: 150 }, (_, i) => ({ id: i }));
    const { calls } = stubFetch({
      [`GET ${PATH}?state=Active&offset=0&limit=100`]: () =>
        json(rows.slice(0, 100), { total: 150 }),
      [`GET ${PATH}?state=Active&offset=100&limit=100`]: () =>
        json(rows.slice(100), { total: 150 }),
    });
    expect(await getAll(`${PATH}?state=Active`)).toEqual(rows);
    expect(calls).toHaveLength(2);
  });
});
