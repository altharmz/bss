// Fetch wrapper for the TMF and internal APIs (same origin: Vite's proxy locally, the
// shared Caddy on the VPS). Every failure becomes an ApiError carrying what the error
// block shows: the call and its outcome, e.g. "GET /tmf-api/... → 503 Service Unavailable".

export class ApiError extends Error {
  readonly method: string;
  readonly path: string;
  readonly status: number; // 0 = no response (network)
  readonly reason: string;
  readonly detail: string; // the TMF Error's `message`: what exactly was wrong ('' if none)

  constructor(method: string, path: string, status: number, reason: string, detail = '') {
    super(`${method} ${path} → ${status === 0 ? '' : `${status} `}${reason}`.trim());
    this.name = 'ApiError';
    this.method = method;
    this.path = path;
    this.status = status;
    this.reason = reason;
    this.detail = detail;
  }
}

/** What to tell an operator about a failed action: the server's own words if it gave
 * any (a 422's message), else the call and its status. */
export function explain(error: unknown): string {
  if (error instanceof ApiError) return error.detail || error.message;
  return error instanceof Error ? error.message : String(error);
}

// HTTP/2 (Caddy) carries no reason phrase, so Response.statusText is often empty; our
// TMF Error bodies carry it as `reason`, and these cover the rest.
const PHRASES: Record<number, string> = {
  400: 'Bad Request',
  401: 'Unauthorized',
  403: 'Forbidden',
  404: 'Not Found',
  405: 'Method Not Allowed',
  409: 'Conflict',
  415: 'Unsupported Media Type',
  422: 'Unprocessable Entity',
  500: 'Internal Server Error',
  502: 'Bad Gateway',
  503: 'Service Unavailable',
  504: 'Gateway Timeout',
};

async function failure(method: string, path: string, response: Response): Promise<ApiError> {
  let reason = '';
  let detail = '';
  try {
    const body: unknown = await response.clone().json();
    if (body && typeof body === 'object') {
      if ('reason' in body && typeof body.reason === 'string') reason = body.reason;
      if ('message' in body && typeof body.message === 'string') detail = body.message;
    }
  } catch {
    // not JSON (a proxy's HTML page, an empty body): fall through
  }
  reason ||= response.statusText || PHRASES[response.status] || 'Error';
  return new ApiError(method, path, response.status, reason, detail === reason ? '' : detail);
}

export async function request(
  method: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      method,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(!['GET', 'HEAD'].includes(method) ? { 'X-Bss-Request': '1' } : {}),
        ...(init.headers as Record<string, string>),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(method, path, 0, 'Network error');
  }
  if (!response.ok) {
    if (response.status === 401 && (path !== '/internal/v1/session' || method === 'DELETE')) {
      window.dispatchEvent(new Event('bss:unauthorized'));
    }
    throw await failure(method, path, response);
  }
  return response;
}

/** POST a JSON body. The caller passes the JSON text when it must control number tokens
 * (amounts); otherwise an object is serialised here. */
export async function postJson<T>(
  path: string,
  body: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await request('POST', path, {
    body: typeof body === 'string' ? body : JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', ...headers },
  });
  return (await response.json()) as T;
}

/** PATCH as JSON Merge Patch - the only PATCH our TMF APIs accept (415 otherwise). As
 * with postJson, a string body is sent as is (exact amount tokens). */
export async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request('PATCH', path, {
    body: typeof body === 'string' ? body : JSON.stringify(body),
    headers: { 'Content-Type': 'application/merge-patch+json' },
  });
  return (await response.json()) as T;
}

/**
 * JSON text in which `amount` - a canonical decimal string from parseAmount, e.g.
 * "1290.50" - is written as a JSON number token verbatim. JSON.stringify could only write
 * a binary float (1290.5); the backend takes the token as an exact Decimal. Put AMOUNT
 * where the number goes.
 */
export const AMOUNT = '\u0000amount\u0000';
// how AMOUNT appears in JSON.stringify's output (it escapes the NULs)
const AMOUNT_TOKEN = JSON.stringify(AMOUNT);
export function jsonWithAmount(body: unknown, amount: string): string {
  if (!/^\d{1,10}\.\d{2}$/.test(amount)) throw new RangeError(`not a canonical amount: ${amount}`);
  const text = JSON.stringify(body);
  if (text.split(AMOUNT_TOKEN).length !== 2)
    throw new Error('the body must hold AMOUNT exactly once');
  return text.replace(AMOUNT_TOKEN, amount);
}

/** jsonWithAmount for a signed amount ("-12.50"): an adjustment's debit is negative. */
export function jsonWithSignedAmount(body: unknown, amount: string): string {
  if (!/^-?\d{1,10}\.\d{2}$/.test(amount))
    throw new RangeError(`not a canonical amount: ${amount}`);
  const text = JSON.stringify(body);
  if (text.split(AMOUNT_TOKEN).length !== 2)
    throw new Error('the body must hold AMOUNT exactly once');
  return text.replace(AMOUNT_TOKEN, amount);
}

/** A fresh Idempotency-Key (1-255 visible ASCII): one per submission, reused only to
 * retry that same submission after an uncertain outcome. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
    return crypto.randomUUID();
  return Array.from({ length: 4 }, () => Math.random().toString(16).slice(2, 10)).join('-');
}

/** The i-th amount slot for jsonWithAmounts (a body with several exact amounts). */
export function amountSlot(i: number): string {
  return `\u0000amount${i}\u0000`;
}

/** jsonWithAmount for several amounts: amounts[i] goes where amountSlot(i) is, each
 * slot exactly once. */
export function jsonWithAmounts(body: unknown, amounts: string[]): string {
  let text = JSON.stringify(body);
  amounts.forEach((amount, i) => {
    if (!/^\d{1,10}\.\d{2}$/.test(amount))
      throw new RangeError(`not a canonical amount: ${amount}`);
    const token = JSON.stringify(amountSlot(i));
    if (text.split(token).length !== 2)
      throw new Error(`the body must hold slot ${i} exactly once`);
    text = text.replace(token, amount);
  });
  return text;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await request('GET', path, signal ? { signal } : {});
  return (await response.json()) as T;
}

export interface Page<T> {
  items: T[];
  total: number; // X-Total-Count: all matching rows, not just this page
}

export async function getPage<T>(path: string, signal?: AbortSignal): Promise<Page<T>> {
  const response = await request('GET', path, signal ? { signal } : {});
  const header = response.headers.get('X-Total-Count');
  const total = header === null || header.trim() === '' ? Number.NaN : Number(header);
  if (!Number.isSafeInteger(total) || total < 0) {
    throw new ApiError('GET', path, response.status, 'missing X-Total-Count');
  }
  return { items: (await response.json()) as T[], total };
}

/** Every row of a paged list, 100 at a time. Stops at MAX_PAGES rather than loop on a
 * list that keeps growing. */
const MAX_PAGES = 100;
export async function getAll<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const items: T[] = [];
  const join = path.includes('?') ? '&' : '?';
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await getPage<T>(`${path}${join}offset=${items.length}&limit=100`, signal);
    items.push(...result.items);
    if (result.items.length === 0 || items.length >= result.total) return items;
  }
  throw new ApiError('GET', path, 200, `more than ${MAX_PAGES * 100} rows`);
}
