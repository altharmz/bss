import type { components } from './generated/internal';
import { getJson, getPage, request } from './client';

const INVOICES = '/internal/v1/wholesale/invoice';
export const WHOLESALE_PAGE = 25;

type Schemas = components['schemas'];
export type Invoice = Schemas['WholesaleInvoice'];
export type Discrepancy = Schemas['WholesaleDiscrepancy'];
export type DiscrepancyKind = Discrepancy['kind'];
export type WholesaleLine = Schemas['WholesaleLine'];

export function loadInvoices(offset: number, signal: AbortSignal) {
  return getPage<Invoice>(`${INVOICES}?offset=${offset}&limit=${WHOLESALE_PAGE}`, signal);
}

export function loadInvoice(id: string, signal: AbortSignal) {
  return getJson<Invoice>(`${INVOICES}/${id}`, signal);
}

export function loadDiscrepancies(
  id: string,
  kind: DiscrepancyKind | '',
  offset: number,
  signal: AbortSignal,
) {
  const query = new URLSearchParams({ offset: String(offset), limit: String(WHOLESALE_PAGE) });
  if (kind) query.set('kind', kind);
  return getPage<Discrepancy>(`${INVOICES}/${id}/discrepancies?${query}`, signal);
}

export function loadWholesaleLines(
  id: string,
  sort: 'margin' | '-margin',
  offset: number,
  signal: AbortSignal,
) {
  return getPage<WholesaleLine>(
    `${INVOICES}/${id}/lines?sort=${sort}&offset=${offset}&limit=${WHOLESALE_PAGE}`,
    signal,
  );
}

export async function importInvoice(number: string, file: File): Promise<Invoice> {
  const response = await request(
    'POST',
    `${INVOICES}?invoiceNumber=${encodeURIComponent(number)}`,
    {
      headers: { 'Content-Type': 'text/csv' },
      body: file,
    },
  );
  return (await response.json()) as Invoice;
}
