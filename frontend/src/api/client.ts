import { ACCOUNT_SECURITY_ERRORS } from './orderErrors';
import { writeLock } from './WriteLock';

// Fetch wrapper for the TMF and internal APIs (same origin: Vite's proxy locally, the
// shared Caddy on the VPS). Every failure becomes an ApiError carrying what the error
// block shows: the call and its outcome, e.g. "GET /tmf-api/... → 503 Service Unavailable".

export class ApiError extends Error {
  readonly method: string;
  readonly path: string;
  readonly status: number; // 0 = no response (network)
  readonly reason: string;
  readonly detail: string; // the TMF Error's `message`: what exactly was wrong ('' if none)
  readonly code: string | undefined; // the TMF Error's business `code` (not a bare status)
  retryAfter: number | undefined; // Retry-After in seconds, when the response sent one

  constructor(
    method: string,
    path: string,
    status: number,
    reason: string,
    detail = '',
    code?: string,
  ) {
    super(`${method} ${path} → ${status === 0 ? '' : `${status} `}${reason}`.trim());
    this.name = 'ApiError';
    this.method = method;
    this.path = path;
    this.status = status;
    this.reason = reason;
    this.detail = detail;
    this.code = code;
    this.retryAfter = undefined;
  }
}

/** What to tell an operator about a failed action: the operator sentence of an account
 * security refusal (`pinRequired`...), else the server's own words if it gave any (a 422's
 * message), else the call and its status. */
export function explain(error: unknown): string {
  if (error instanceof ApiError) {
    const known = error.code === undefined ? undefined : ACCOUNT_SECURITY_ERRORS[error.code];
    return known ?? (error.detail || error.message);
  }
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
  let code: string | undefined;
  try {
    const body: unknown = await response.clone().json();
    if (body && typeof body === 'object') {
      if ('reason' in body && typeof body.reason === 'string') reason = body.reason;
      if ('message' in body && typeof body.message === 'string') detail = body.message;
      // a business code replaces the status there (TmfError(code=...)); a bare status is none
      if ('code' in body && typeof body.code === 'string' && !/^\d+$/.test(body.code))
        code = body.code;
    }
  } catch {
    // not JSON (a proxy's HTML page, an empty body): fall through
  }
  reason ||= response.statusText || PHRASES[response.status] || 'Error';
  const error = new ApiError(
    method,
    path,
    response.status,
    reason,
    detail === reason ? '' : detail,
    code,
  );
  const retryAfter = Number(response.headers.get('Retry-After') ?? '');
  if (response.headers.has('Retry-After') && Number.isFinite(retryAfter))
    error.retryAfter = retryAfter;
  return error;
}

// Reads under the app's request bound (issue #412; docs/decisions/frontend.md, "Reads
// under the request bound"). One app process admits BSS_MAX_IN_FLIGHT requests (10 on the
// VPS) and answers the rest at once with 503 `overloaded` and Retry-After. A screen like
// the subscriber card starts a dozen reads together, so a tab sends at most READ_SLOTS
// reads at a time (the rest wait their turn, in order), and a read refused as overloaded
// is sent again after Retry-After, backing off, up to OVERLOAD_RETRIES times; after that
// the error shows with its Retry. A waiting or backing-off read holds no slot and stops
// as soon as its signal aborts. Writes are neither queued nor resent: they keep their own
// uncertain-outcome rules.
export const READ_SLOTS = 4;
export const OVERLOAD_RETRIES = 4;

function abortError(signal?: AbortSignal | null): unknown {
  return signal?.reason instanceof DOMException
    ? signal.reason
    : new DOMException('The operation was aborted.', 'AbortError');
}

class ReadGate {
  private active = 0;
  private readonly waiting: (() => void)[] = [];

  constructor(private readonly slots: number) {}

  private grant(): () => void {
    this.active++;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.active--;
      this.waiting.shift()?.();
    };
  }

  /** A release function at once when a slot is free (no await: the fetch starts in the
   * caller's tick, as without the gate), else null. */
  take(): (() => void) | null {
    return this.active < this.slots ? this.grant() : null;
  }

  /** Waits in line for a slot; rejects if `signal` aborts first. */
  wait(signal?: AbortSignal | null): Promise<() => void> {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(abortError(signal));
        return;
      }
      const cancel = () => {
        const index = this.waiting.indexOf(turn);
        if (index >= 0) this.waiting.splice(index, 1);
        reject(abortError(signal));
      };
      const turn = () => {
        signal?.removeEventListener('abort', cancel);
        resolve(this.grant());
      };
      this.waiting.push(turn);
      signal?.addEventListener('abort', cancel, { once: true });
    });
  }

  /** Tests only: forget every slot and waiter (a stub that never answers holds its slot). */
  reset(): void {
    this.active = 0;
    this.waiting.length = 0;
  }
}

const readGate = new ReadGate(READ_SLOTS);

/** Tests only (src/test/setup.ts): a fresh gate for every test. */
export function resetReadGate(): void {
  readGate.reset();
}

/** Waits `ms`, or rejects with an AbortError as soon as `signal` aborts. */
function pause(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError(signal));
      return;
    }
    const stop = () => {
      clearTimeout(timer);
      reject(abortError(signal));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', stop);
      resolve();
    }, ms);
    signal?.addEventListener('abort', stop, { once: true });
  });
}

/** The wait before resending an overloaded read: Retry-After (at least 1 s), doubled per
 * retry, plus up to half again at random so a tab's refused reads do not return together. */
function overloadDelay(retryAfter: number | undefined, retry: number): number {
  const base = Math.max(retryAfter ?? 1, 1) * 1000 * 2 ** retry;
  return base + Math.random() * base * 0.5;
}

export async function request(
  method: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  if (!['GET', 'HEAD'].includes(method)) return send(method, path, init);
  for (let retry = 0; ; retry++) {
    if (init.signal?.aborted) throw abortError(init.signal);
    const release = readGate.take() ?? (await readGate.wait(init.signal));
    let refused: ApiError;
    try {
      return await send(method, path, init);
    } catch (error) {
      const transient =
        error instanceof ApiError && error.status === 503 && error.code === 'overloaded';
      if (!transient || retry >= OVERLOAD_RETRIES) throw error;
      refused = error;
    } finally {
      release();
    }
    await pause(overloadDelay(refused.retryAfter, retry), init.signal);
  }
}

async function send(method: string, path: string, init: RequestInit): Promise<Response> {
  const writing = !['GET', 'HEAD'].includes(method);
  const release = writing ? writeLock.begin() : () => {};
  if (writing) writeLock.requested();
  try {
    let response: Response;
    try {
      response = await fetch(path, {
        ...init,
        method,
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          ...(writing ? { 'X-Bss-Request': '1' } : {}),
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
    if (
      response.ok &&
      ((path === '/tmf-api/productOrderingManagement/v5/productOrder' && method === 'POST') ||
        (/^\/tmf-api\/(accountManagement\/v5\/billingAccount|productCatalogManagement\/v5\/productOffering)(\/[^/]+)?$/.test(
          path,
        ) &&
          ['POST', 'DELETE'].includes(method)))
    )
      window.dispatchEvent(new Event('bss:countsChanged'));
    return response;
  } finally {
    release();
  }
}

/** The customer's account PIN as the `X-Account-Pin` header (docs/decisions/accounts.md):
 * sent only when typed, never in a body or URL. */
export function accountPinHeader(pin: string): Record<string, string> {
  const trimmed = pin.trim();
  return trimmed ? { 'X-Account-Pin': trimmed } : {};
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
export async function patchJson<T>(
  path: string,
  body: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await request('PATCH', path, {
    body: typeof body === 'string' ? body : JSON.stringify(body),
    headers: { 'Content-Type': 'application/merge-patch+json', ...headers },
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
  // Whether rows follow this page: the app sends `Link: <...>; rel="next"`. Lists carry no
  // total (#312, decided 2026-11-10), so screens page with Next/Previous only.
  more: boolean;
}

export async function getPage<T>(path: string, signal?: AbortSignal): Promise<Page<T>> {
  const response = await request('GET', path, signal ? { signal } : {});
  const more = /;\s*rel="next"/.test(response.headers.get('Link') ?? '');
  return { items: (await response.json()) as T[], more };
}

/** Every row of a paged list, 100 at a time. Stops at MAX_PAGES rather than loop on a
 * list that keeps growing. */
const MAX_PAGES = 100;
export async function getAll<T>(path: string, signal?: AbortSignal): Promise<T[]> {
  const items: T[] = [];
  // a path may end in a bare `?` (an empty filter query): nothing to join then
  const join = path.endsWith('?') ? '' : path.includes('?') ? '&' : '?';
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await getPage<T>(`${path}${join}offset=${items.length}&limit=100`, signal);
    items.push(...result.items);
    if (result.items.length === 0 || !result.more) return items;
  }
  throw new ApiError('GET', path, 200, `more than ${MAX_PAGES * 100} rows`);
}
