import type { components as T637 } from './generated/tmf637';
import { getAll, getJson } from './client';
import { PATHS } from './bss';

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

export function loadProducts(
  filter: { status: string | null; account: string | null },
  signal: AbortSignal,
) {
  const query = [
    ...(filter.account !== null ? [`billingAccount.id=${filter.account}`] : []),
    ...(filter.status !== null ? [`status=${filter.status}`] : []),
  ];
  return getAll<InventoryProduct>(
    query.length ? `${PATHS.products}?${query.join('&')}` : PATHS.products,
    signal,
  );
}

export function loadProduct(id: string, signal: AbortSignal) {
  return getJson<InventoryProduct>(`${PATHS.products}/${id}`, signal);
}
