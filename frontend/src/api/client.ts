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
      headers: { Accept: 'application/json', ...(init.headers as Record<string, string>) },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(method, path, 0, 'Network error');
  }
  if (!response.ok) throw await failure(method, path, response);
  return response;
}

/** POST a JSON body. The caller passes the JSON text when it must control number tokens
 * (amounts); otherwise an object is serialised here. */
export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request('POST', path, {
    body: typeof body === 'string' ? body : JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  });
  return (await response.json()) as T;
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
