import { vi } from 'vitest';

// A fetch stub for component tests: maps "METHOD path" (path with query) to a handler.
// An unmatched request fails the test loudly instead of hanging: the fetch rejects (the
// UI shows a network error) and setup.ts fails the test after it ends, so a missing stub
// can never decide a test by timing - whether its error rendered before an assertion.

const unexpected: string[] = [];

/** The unmatched requests since the last call, cleared (setup.ts, after every test). */
export function takeUnexpectedRequests(): string[] {
  return unexpected.splice(0);
}

/** An answer to one request; a prefix route (`'GET /path?*'`) gets the request URL. */
export type Handler = (url?: string) => Response | Promise<Response>;

/** A JSON answer. `total` describes a list: the rows the server holds beyond this page
 * make it send `Link: rel="next"` (the app sends no total, #312); `more` says so directly. */
export function json(
  body: unknown,
  init: ResponseInit & { total?: number; more?: boolean } = {},
): Response {
  const { total, more, ...rest } = init;
  const headers = new Headers(rest.headers);
  headers.set('Content-Type', 'application/json');
  if (total !== undefined || more !== undefined) {
    const count = Array.isArray(body) ? body.length : 1;
    headers.set('X-Result-Count', String(count));
    if (more ?? (total !== undefined && total > count)) {
      headers.set('Link', '</next>; rel="next"');
    }
  }
  return new Response(JSON.stringify(body), { status: 200, ...rest, headers });
}

/** A request the test does not care about the answer to (e.g. the screen it navigates to
 * loads data): it stays in flight, so it can neither render nor fail anything. */
export const pending: Handler = () => new Promise<Response>(() => {});

export function tmfError(status: number, reason: string): Response {
  return json(
    { '@type': 'Error', code: String(status), reason, message: reason, status: String(status) },
    { status },
  );
}

// The subscriber card's Payment methods panel (issue 185b) loads the saved cards and the
// auto top-up rule on every render; a test that is not about them gets an empty answer.
function paymentPanelDefault(key: string): Handler | undefined {
  if (key.startsWith('GET /tmf-api/paymentMethod/v4/paymentMethod?account.id='))
    return () => json([], { total: 0 });
  if (/^GET \/internal\/v1\/billingAccount\/[^/]+\/autoTopup$/.test(key))
    return () =>
      json({
        enabled: false,
        threshold: null,
        amount: null,
        defaultPaymentMethod: null,
        changedAt: null,
      });
  return undefined;
}

// The order and product dialogs list the network commands of the order or product
// (issue 205); a test that is not about them gets an empty answer.
function networkDefault(key: string): Handler | undefined {
  if (
    /^GET \/internal\/v1\/network\/command\?(productOrder|product)\.id=[^&]+&offset=0&limit=100$/.test(
      key,
    )
  )
    return () => json([], { total: 0 });
  return undefined;
}

export function stubFetch(routes: Record<string, Handler>) {
  const calls: string[] = [];
  routes = { [NAV_COMMANDS]: noCommands, ...routes };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const key = `${init?.method ?? 'GET'} ${url}`;
    calls.push(key);
    const prefix = Object.keys(routes).find(
      (route) => route.endsWith('*') && key.startsWith(route.slice(0, -1)),
    );
    const handler =
      routes[key] ??
      (prefix ? routes[prefix] : undefined) ??
      paymentPanelDefault(key) ??
      networkDefault(key);
    if (!handler) {
      unexpected.push(key);
      throw new Error(`unexpected request in test: ${key}`);
    }
    return handler(url);
  });
  vi.stubGlobal('fetch', fetchMock);
  return { calls, fetchMock };
}

/** The sidebar's Billing runs item (failed badge) loads the latest run on every
 * screen; tests that do not care stub it with no runs. */
export const NAV_RUNS = 'GET /internal/v1/billingRun?limit=1';
export const noRuns: Handler = () => json([], { total: 0 });

/** The sidebar's Prepaid commands badge (failedTechnical, one row): stubbed with none by
 * default in stubFetch; a test of the badge overrides it. */
export const NAV_COMMANDS = 'GET /internal/v1/prepaid/command?state=failedTechnical&limit=1';
export const noCommands: Handler = () => json([], { total: 0 });

export const CONTEXT = {
  businessDate: '2026-10-01',
  now: '2026-10-01T09:00:00Z',
  timeZone: 'Europe/Moscow',
  utcOffset: '+03:00',
  environment: 'prod',
  operator: 'i.petrova',
  currency: 'USD',
  billingRunAt: '00:30',
  paymentTermDays: 30,
};
