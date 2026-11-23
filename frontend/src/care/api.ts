export class CareError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** The customer cookie is scoped to /self-care/. Keep this client independent of admin sessions. */
export async function careRequest<T>(
  path: string,
  method = 'GET',
  body?: unknown,
  idempotencyKey?: string,
): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' });
  if (method !== 'GET') headers.set('X-Bss-Request', '1');
  if (body !== undefined) headers.set('Content-Type', 'application/json');
  if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey);
  const response = await fetch('/self-care/v1' + path, {
    method,
    credentials: 'same-origin',
    headers,
    body: body === undefined ? null : typeof body === 'string' ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new CareError(
      response.status,
      error?.message ?? `Request failed (HTTP ${response.status})`,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
