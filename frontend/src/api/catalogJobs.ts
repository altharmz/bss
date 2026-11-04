// TMF620 importJob / exportJob: the catalog as one JSON document carried inline as a
// `data:application/json;base64,...` URL. Both jobs finish within their request.
// Design: docs/ui.md.

import { getAll, postJson, request } from './client';

export const CATALOG_BASE = '/tmf-api/productCatalogManagement/v5';
export const MAX_DOCUMENT_BYTES = 1_000_000; // of the JSON document, before base64
const DATA_PREFIX = 'data:application/json;base64,';

export type JobKind = 'ImportJob' | 'ExportJob';

interface JobBody {
  id?: string;
  '@type'?: string;
  status?: string;
  query?: string;
  url?: string;
  creationDate?: string;
  completionDate?: string;
  errorLog?: string;
}

export interface CatalogJob {
  id: string;
  kind: JobKind;
  status: string;
  query: string | null;
  url: string | null;
  created: string | null;
  completed: string | null;
  errorLog: string | null;
}

export function toJob(body: JobBody, kind: JobKind): CatalogJob {
  if (!body.id) throw new Error('The server sent no job id');
  return {
    id: body.id,
    kind,
    status: body.status ?? '',
    query: body.query ?? null,
    url: body.url ?? null,
    created: body.creationDate ?? null,
    completed: body.completionDate ?? null,
    errorLog: body.errorLog ?? null,
  };
}

const resource = (kind: JobKind) => (kind === 'ImportJob' ? 'importJob' : 'exportJob');

/** Both kinds, newest first. */
export async function loadJobs(signal: AbortSignal): Promise<CatalogJob[]> {
  const [imports, exports] = await Promise.all([
    getAll<JobBody>(`${CATALOG_BASE}/importJob`, signal),
    getAll<JobBody>(`${CATALOG_BASE}/exportJob`, signal),
  ]);
  return [
    ...imports.map((b) => toJob(b, 'ImportJob')),
    ...exports.map((b) => toJob(b, 'ExportJob')),
  ].sort((a, b) => (b.created ?? '').localeCompare(a.created ?? ''));
}

export interface CatalogChoice {
  id: string;
  name: string;
}

export async function loadCatalogChoices(signal: AbortSignal): Promise<CatalogChoice[]> {
  const found = await getAll<{ id?: string; name?: string }>(
    `${CATALOG_BASE}/productCatalog`,
    signal,
  );
  return found.flatMap((c) => (c.id ? [{ id: c.id, name: c.name ?? c.id }] : []));
}

/** `query` is empty (everything) or `productCatalog.id=<id>`; empty is not sent. */
export function exportBody(catalogId: string): Record<string, unknown> {
  return {
    '@type': 'ExportJob',
    path: CATALOG_BASE,
    contentType: 'application/json',
    ...(catalogId === '' ? {} : { query: `productCatalog.id=${catalogId}` }),
  };
}

export async function createExportJob(catalogId: string): Promise<CatalogJob> {
  return toJob(
    await postJson<JobBody>(`${CATALOG_BASE}/exportJob`, exportBody(catalogId)),
    'ExportJob',
  );
}

export function utf8Bytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

/** The text as a `data:application/json;base64,...` URL (UTF-8). */
export function toDataUrl(text: string): string {
  const bytes = utf8Bytes(text);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192)
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return DATA_PREFIX + btoa(binary);
}

/** The document inside a data: URL, or null when it is not one. */
export function fromDataUrl(url: string): string | null {
  if (!url.startsWith(DATA_PREFIX)) return null;
  const binary = atob(url.slice(DATA_PREFIX.length));
  return new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0)));
}

/** Why the text cannot be imported, or null when it can be sent: JSON, at most 1 MB. */
export function checkDocument(text: string): string | null {
  if (text.trim() === '') return 'Paste or choose a JSON document';
  if (utf8Bytes(text).length > MAX_DOCUMENT_BYTES)
    return `The document is larger than ${MAX_DOCUMENT_BYTES.toLocaleString('en-US')} bytes`;
  try {
    JSON.parse(text);
  } catch {
    return 'Choose a file exported from a catalog export';
  }
  return null;
}

export function importBody(text: string): Record<string, unknown> {
  return {
    '@type': 'ImportJob',
    path: CATALOG_BASE,
    contentType: 'application/json',
    url: toDataUrl(text),
  };
}

export async function createImportJob(text: string): Promise<CatalogJob> {
  return toJob(await postJson<JobBody>(`${CATALOG_BASE}/importJob`, importBody(text)), 'ImportJob');
}

export async function deleteJob(job: CatalogJob): Promise<void> {
  await request('DELETE', `${CATALOG_BASE}/${resource(job.kind)}/${job.id}`);
}

/** The export's document as a file: the data URL decoded into a Blob. */
export function downloadBlob(job: CatalogJob): Blob | null {
  if (job.url === null) return null;
  const text = fromDataUrl(job.url);
  return text === null ? null : new Blob([text], { type: 'application/json' });
}

export const downloadName = (date: string) => `bss-catalog-${date}.json`;

export function saveBlob(blob: Blob, name: string): void {
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
}
