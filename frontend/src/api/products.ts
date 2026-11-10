import type { components as T637 } from './generated/tmf637';
import { getJson, getPage } from './client';
import { PATHS } from './bss';

const NUMBERS = '/tmf-api/resourceInventory/v5/resource';

// The global Products screen (docs/decisions/frontend.md, slice 21): TMF637 reads only.
// Products are added and ended by orders alone.

export type InventoryProduct = T637['schemas']['Product'];

/** Every status the service stores (app/repository/products.py); the filter offers these. */
export const PRODUCT_STATES = [
  'created',
  'pendingActive',
  'active',
  'pendingTerminate',
  'suspended',
  'terminated',
  'cancelled',
  'aborted',
] as const;

export const PRODUCT_PAGE = 50;

export interface ProductFilter {
  status: string | null;
  account: string | null;
  /** A subscriber number, exactly as stored (E.164 digits). */
  number: string | null;
}

/**
 * One page of products, oldest first (#314): status, account and number are server
 * filters. A number is looked up first (resource `value=`) and the products are those it
 * realizes; an unknown number is an empty page.
 */
export async function loadProductPage(
  filter: ProductFilter,
  offset: number,
  signal: AbortSignal,
): Promise<{ items: InventoryProduct[]; more: boolean; offset: number }> {
  const query = new URLSearchParams();
  if (filter.account !== null) query.set('billingAccount.id', filter.account);
  if (filter.status !== null) query.set('status', filter.status);
  if (filter.number !== null) {
    const found = await getPage<{ id?: string }>(
      `${NUMBERS}?value=${encodeURIComponent(filter.number)}&limit=1`,
      signal,
    );
    const id = found.items[0]?.id;
    if (id === undefined) return { items: [], more: false, offset };
    query.set('realizingResource.id', id);
  }
  query.set('offset', String(offset));
  query.set('limit', String(PRODUCT_PAGE));
  const page = await getPage<InventoryProduct>(`${PATHS.products}?${query}`, signal);
  return { ...page, offset };
}

/** The digits of the numbers these products hold, by resource id: one read per number. */
export async function loadNumberValues(
  products: InventoryProduct[],
  signal: AbortSignal,
): Promise<Record<string, string>> {
  const ids = [
    ...new Set(products.flatMap((p) => (p.realizingResource ?? []).map((r) => r.id ?? ''))),
  ].filter(Boolean);
  const bodies = await Promise.all(
    ids.map((id) => getJson<{ id?: string; value?: string }>(`${NUMBERS}/${id}`, signal)),
  );
  return Object.fromEntries(bodies.map((b, i) => [ids[i] as string, b.value ?? '']));
}

export function loadProduct(id: string, signal: AbortSignal) {
  return getJson<InventoryProduct>(`${PATHS.products}/${id}`, signal);
}
