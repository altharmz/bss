import { vi } from 'vitest';

// A fetch stub for component tests: maps "METHOD path" (path with query) to a handler.
// An unmatched request fails the test loudly instead of hanging.

export type Handler = () => Response | Promise<Response>;

export function json(body: unknown, init: ResponseInit & { total?: number } = {}): Response {
  const { total, ...rest } = init;
  const headers = new Headers(rest.headers);
  headers.set('Content-Type', 'application/json');
  if (total !== undefined) {
    headers.set('X-Total-Count', String(total));
    headers.set('X-Result-Count', String(Array.isArray(body) ? body.length : 1));
  }
  return new Response(JSON.stringify(body), { status: 200, ...rest, headers });
}

export function tmfError(status: number, reason: string): Response {
  return json(
    { '@type': 'Error', code: String(status), reason, message: reason, status: String(status) },
    { status },
  );
}

export function stubFetch(routes: Record<string, Handler>) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const key = `${init?.method ?? 'GET'} ${url}`;
    calls.push(key);
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected request in test: ${key}`);
    return handler();
  });
  vi.stubGlobal('fetch', fetchMock);
  return { calls, fetchMock };
}

export const CONTEXT = {
  businessDate: '2026-10-01',
  now: '2026-10-01T09:00:00Z',
  timeZone: 'Europe/Moscow',
  utcOffset: '+03:00',
  environment: 'prod',
  operator: 'i.petrova',
  currency: 'RUB',
  billingRunAt: '00:30',
};
