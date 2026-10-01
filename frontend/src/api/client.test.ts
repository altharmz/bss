import { describe, expect, it } from 'vitest';
import { json, stubFetch, tmfError } from '../test/fetch';
import { ApiError, getAll, getJson, getPage } from './client';

const PATH = '/tmf-api/accountManagement/v5/billingAccount';

describe('errors', () => {
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
