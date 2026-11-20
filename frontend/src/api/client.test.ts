import { afterEach, describe, expect, it, vi } from 'vitest';
import { json, overloadedError, pending, stubFetch, tmfCodeError, tmfError } from '../test/fetch';
import {
  accountPinHeader,
  AMOUNT,
  ApiError,
  explain,
  getAll,
  getJson,
  getPage,
  jsonWithAmount,
  OVERLOAD_RETRIES,
  postJson,
  READ_SLOTS,
} from './client';

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

  it('keeps a business code and explains an account security refusal in operator words', async () => {
    stubFetch({ [`GET ${PATH}`]: () => tmfCodeError(403, 'pinLocked', 'too many wrong PINs') });
    const error = (await getJson(PATH).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe('pinLocked');
    expect(explain(error)).toMatch(/^Too many wrong account PINs/);
    // a bare status in `code` is no business code; an unknown code keeps the server's words
    stubFetch({ [`GET ${PATH}`]: () => tmfError(409, 'Conflict') });
    expect(((await getJson(PATH).catch((e: unknown) => e)) as ApiError).code).toBeUndefined();
    stubFetch({ [`GET ${PATH}`]: () => tmfCodeError(403, 'somethingNew', 'a new refusal') });
    expect(explain(await getJson(PATH).catch((e: unknown) => e))).toBe('a new refusal');
  });

  it('sends the account PIN header only when one is typed', () => {
    expect(accountPinHeader(' 482913 ')).toEqual({ 'X-Account-Pin': '482913' });
    expect(accountPinHeader('  ')).toEqual({});
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
  it('reads whether more rows follow from Link rel="next" (#312: lists carry no total)', async () => {
    stubFetch({ [`GET ${PATH}?limit=1`]: () => json([{ id: 'a' }], { more: true }) });
    expect(await getPage(`${PATH}?limit=1`)).toEqual({ items: [{ id: 'a' }], more: true });
  });

  it('a list without a Link header is its last page', async () => {
    stubFetch({ [`GET ${PATH}`]: () => json([{ id: 'a' }], { more: false }) });
    expect(await getPage(PATH)).toEqual({ items: [{ id: 'a' }], more: false });
  });

  it('getAll walks the pages 100 at a time until no Link follows', async () => {
    const rows = Array.from({ length: 150 }, (_, i) => ({ id: i }));
    const { calls } = stubFetch({
      [`GET ${PATH}?state=Active&offset=0&limit=100`]: () =>
        json(rows.slice(0, 100), { more: true }),
      [`GET ${PATH}?state=Active&offset=100&limit=100`]: () =>
        json(rows.slice(100), { more: false }),
    });
    expect(await getAll(`${PATH}?state=Active`)).toEqual(rows);
    expect(calls).toHaveLength(2);
  });

  it('getAll joins the paging to a path that ends in a bare question mark', async () => {
    stubFetch({ [`GET ${PATH}?offset=0&limit=100`]: () => json([{ id: 1 }], { total: 1 }) });
    expect(await getAll(`${PATH}?`)).toEqual([{ id: 1 }]);
  });
});

// Issue #412: reads under the app's request bound (app/overload.py).
describe('reads under the request bound', () => {
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const fake = () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    vi.spyOn(Math, 'random').mockReturnValue(0); // the backoff with no jitter
  };
  afterEach(() => vi.restoreAllMocks());

  it('sends at most READ_SLOTS reads at a time, the rest in order as slots free', async () => {
    const answers: ((response: Response) => void)[] = [];
    const { calls } = stubFetch({
      [`GET ${PATH}/*`]: () => new Promise<Response>((resolve) => answers.push(resolve)),
    });
    const reads = Array.from({ length: READ_SLOTS + 2 }, (_, i) => getJson(`${PATH}/${i}`));
    await settle();
    expect(calls).toEqual(Array.from({ length: READ_SLOTS }, (_, i) => `GET ${PATH}/${i}`));
    answers[1]?.(json({ id: 1 }));
    await settle();
    expect(calls.at(-1)).toBe(`GET ${PATH}/${READ_SLOTS}`);
    answers.forEach((answer, i) => answer(json({ id: i })));
    await settle();
    answers.forEach((answer, i) => answer(json({ id: i })));
    expect(await Promise.all(reads)).toEqual(reads.map((_, i) => ({ id: i })));
    expect(calls).toHaveLength(READ_SLOTS + 2);
  });

  it('drops a waiting read whose signal aborts, without sending it', async () => {
    const { calls } = stubFetch({ [`GET ${PATH}/*`]: pending });
    for (let i = 0; i < READ_SLOTS; i++) void getJson(`${PATH}/${i}`);
    const controller = new AbortController();
    const waiting = getJson(`${PATH}/late`, controller.signal);
    controller.abort();
    await expect(waiting).rejects.toMatchObject({ name: 'AbortError' });
    await settle();
    expect(calls).not.toContain(`GET ${PATH}/late`);
  });

  it('sends an overloaded read again after Retry-After and returns its answer', async () => {
    fake();
    let n = 0;
    const { calls } = stubFetch({
      [`GET ${PATH}`]: () => (n++ === 0 ? overloadedError() : json({ id: 'a' })),
    });
    const read = getJson(PATH);
    await vi.advanceTimersByTimeAsync(999);
    expect(calls).toHaveLength(1); // waits the full Retry-After
    await vi.advanceTimersByTimeAsync(1);
    expect(await read).toEqual({ id: 'a' });
    expect(calls).toHaveLength(2);
  });

  it('gives up after OVERLOAD_RETRIES, backing off, and then sends nothing more', async () => {
    fake();
    const { calls } = stubFetch({ [`GET ${PATH}`]: overloadedError });
    const read = getJson(PATH).catch((e: unknown) => e);
    const waits: number[] = [];
    for (let retry = 0; retry < OVERLOAD_RETRIES; retry++) {
      const before = calls.length;
      let waited = 0;
      while (calls.length === before) {
        await vi.advanceTimersByTimeAsync(500);
        waited += 500;
      }
      waits.push(waited);
    }
    expect(waits).toEqual([1000, 2000, 4000, 8000]);
    const error = (await read) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.code, error.message]).toEqual([
      503,
      'overloaded',
      `GET ${PATH} → 503 Service Unavailable`,
    ]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(OVERLOAD_RETRIES + 1);
  });

  it('stops backing off when the read is aborted', async () => {
    fake();
    const { calls } = stubFetch({ [`GET ${PATH}`]: overloadedError });
    const controller = new AbortController();
    const read = getJson(PATH, controller.signal).catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    expect(await read).toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toHaveLength(1);
  });

  it('does not resend a 503 that is not the overload refusal', async () => {
    const { calls } = stubFetch({ [`GET ${PATH}`]: () => tmfError(503, 'Service Unavailable') });
    await expect(getJson(PATH)).rejects.toThrow('503');
    expect(calls).toHaveLength(1);
  });

  it('leaves writes alone: never queued behind reads, never resent', async () => {
    const { calls } = stubFetch({
      [`GET ${PATH}/*`]: pending,
      [`POST ${PATH}`]: overloadedError,
    });
    for (let i = 0; i < READ_SLOTS; i++) void getJson(`${PATH}/${i}`);
    const error = (await postJson(PATH, {}).catch((e: unknown) => e)) as ApiError;
    expect([error.status, error.code]).toEqual([503, 'overloaded']);
    expect(calls.filter((call) => call.startsWith('POST'))).toEqual([`POST ${PATH}`]);
  });
});
