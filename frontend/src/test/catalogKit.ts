// Shared fixtures of the Catalog.*.test.tsx files: one screen, several files, so vitest runs them side by side (#319).
import { createElement } from 'react';
import { render, screen, within } from '@testing-library/react';
import { App } from '../app/App';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, type Handler } from './fetch';

export const CAT = '/tmf-api/productCatalogManagement/v5';
export const usd = (value: number) => ({ unit: 'USD', value });
export const ids = (n: number) => `0000000${n}-0000-4000-8000-00000000000${n}`;
export const code = (id: string) => [{ id, externalIdentifierType: 'offeringCode' }];

export const OFFERINGS = [
  {
    id: ids(1),
    name: 'Mobile L',
    lifecycleStatus: 'Active',
    externalIdentifier: code('MOB-L'),
    productSpecification: { name: 'Mobile Voice & Data' },
    validFor: { startDateTime: '2026-06-01T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr1' }],
  },
  {
    id: ids(2),
    name: 'Fiber 100',
    lifecycleStatus: 'Active',
    externalIdentifier: code('NET-100'),
    productSpecification: { name: 'Home Internet' },
    validFor: { startDateTime: '2026-06-01T21:00:00Z', endDateTime: '2026-12-31T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr2' }, { id: 'pr3' }],
  },
  {
    id: ids(3),
    name: 'TV Basic',
    lifecycleStatus: 'In Design',
    externalIdentifier: code('TV-BASE'),
    productSpecification: { name: 'TV' },
    validFor: { startDateTime: '2026-10-04T21:00:00Z' },
    productOfferingPrice: [{ id: 'pr4' }],
  },
  {
    id: ids(4),
    name: 'Old Mobile',
    lifecycleStatus: 'Retired',
    productSpecification: { name: 'Mobile Voice & Data' },
    validFor: { startDateTime: '2026-01-01T21:00:00Z', endDateTime: '2026-07-01T21:00:00Z' },
    productOfferingPrice: [],
  },
];
export const PRICES = [
  { id: 'pr1', price: usd(1290), validFor: { startDateTime: '2026-06-01T21:00:00Z' } },
  // 650 now, 700 from tomorrow: the current one is shown
  {
    id: 'pr2',
    price: usd(650),
    validFor: { startDateTime: '2026-06-01T21:00:00Z', endDateTime: '2026-10-01T21:00:00Z' },
  },
  { id: 'pr3', price: usd(700), validFor: { startDateTime: '2026-10-01T21:00:00Z' } },
  // only a future price: shown with its start
  { id: 'pr4', price: usd(399), validFor: { startDateTime: '2026-10-04T21:00:00Z' } },
];
export const SPECS = [
  { id: 's1', name: 'Mobile Voice & Data' },
  { id: 's2', name: 'Home Internet' },
  { id: 's3', name: 'TV' },
];

export function backend(routes: Record<string, Handler> = {}, offerings: unknown[] = OFFERINGS) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    [`GET ${CAT}/productOffering?limit=1&fields=id`]: () => json([], { total: offerings.length }),
    [`GET ${CAT}/productOffering?offset=0&limit=100`]: () =>
      json(offerings, { total: offerings.length }),
    [`GET ${CAT}/productSpecification?offset=0&limit=100`]: () =>
      json(SPECS, { total: SPECS.length }),
    [`GET ${CAT}/category?offset=0&limit=100`]: () => json([], { total: 0 }),
    [`GET ${CAT}/productOfferingPrice?priceType=recurring&offset=0&limit=100`]: () =>
      json(PRICES, { total: PRICES.length }),
    [`GET ${CAT}/productOfferingPrice?priceType=oneTime&offset=0&limit=100`]: () =>
      json([], { total: 0 }),
    // the detail dialog loads the offering itself
    ...Object.fromEntries(
      (offerings as { id: string }[]).map((o) => [
        `GET ${CAT}/productOffering/${o.id}`,
        () => json(o),
      ]),
    ),
    ...routes,
  });
}

export async function open() {
  window.history.replaceState(null, '', '/catalog');
  render(createElement(App));
  await screen.findByRole('heading', { name: 'Product catalog' });
}

export const dataRows = () =>
  screen.getAllByRole('row').filter((row) => within(row).queryAllByRole('cell').length === 5);

export function row(name: string): HTMLElement {
  const found = dataRows().find((r) => within(r).queryByText(name));
  if (!found) throw new Error(`no row ${name}`);
  return found;
}

export const cellsOf = (name: string) =>
  within(row(name))
    .getAllByRole('cell')
    .map((c) => c.textContent);
