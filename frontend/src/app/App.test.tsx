import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, pending, stubFetch, tmfError, type Handler } from '../test/fetch';
import { RUN } from '../test/fixtures';
import { App } from './App';
import { parseRoute } from './router';

const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id';
const OFFERINGS = '/tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id';
const ID = '1f3a9c2b-0000-4000-8000-000000000001';
const TMF = '/tmf-api';
const empty = () => json([], { total: 0 });

// Every request a screen of these tests makes is stubbed: an unstubbed one fails the test
// (setup.ts). Before, the subscribers list's own load went unstubbed and its network
// error raced the "no alert after Retry" assertion (failed 2 of 5 runs on master).
function backend(
  context: Handler = () => json(CONTEXT),
  latestRun: Handler = () => json([{ ...RUN, status: 'completed' }], { total: 9 }),
) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    'GET /internal/v1/context': context,
    [NAV_RUNS]: latestRun,
    // the billing runs screen: no runs (the screen has its own tests)
    'GET /internal/v1/billingRun?limit=14': empty,
    [`GET ${ACCOUNTS}`]: () => json([{ id: ID }], { total: 1284 }),
    [`GET ${OFFERINGS}`]: () => json([], { total: 12 }),
    // the subscribers list: no accounts
    [`GET ${TMF}/accountManagement/v5/billingAccount?offset=0&limit=100`]: empty,
    [`GET ${TMF}/productInventory/v5/product?status=active&fields=billingAccount&offset=0&limit=100`]:
      empty,
    // the catalog: empty
    [`GET ${TMF}/productCatalogManagement/v5/productOffering?offset=0&limit=100`]: empty,
    [`GET ${TMF}/productCatalogManagement/v5/productOfferingPrice?priceType=recurring&offset=0&limit=100`]:
      empty,
    [`GET ${TMF}/productCatalogManagement/v5/productSpecification?offset=0&limit=100`]: empty,
    // a subscriber card: only its breadcrumbs matter here
    [`GET ${TMF}/accountManagement/v5/billingAccount/${ID}`]: pending,
  });
}

function start(path: string) {
  window.history.replaceState(null, '', path);
  render(<App />);
}

describe('routes', () => {
  it.each([
    ['/subscribers', { name: 'subscribers' }],
    [`/subscribers/${ID}`, { name: 'subscriber', accountId: ID }],
    [`/subscribers/${ID.toUpperCase()}`, { name: 'subscriber', accountId: ID }],
    ['/catalog', { name: 'catalog' }],
    ['/billing-runs', { name: 'billingRuns' }],
    ['/financial-accounts', { name: 'financialAccounts' }],
    ['/billing', { name: 'notFound', path: '/billing' }], // not /billing: /billing-status
    ['/subscribers/nope', { name: 'notFound', path: '/subscribers/nope' }],
  ])('%s', (path, route) => {
    expect(parseRoute(path)).toEqual(route);
  });

  it('sends / to the subscribers list', async () => {
    backend();
    start('/');
    expect(window.location.pathname).toBe('/subscribers');
    expect(await screen.findByRole('heading', { name: 'Subscribers' })).toBeTruthy();
  });

  it('navigates in place on a nav click and follows the back button', async () => {
    backend();
    start('/subscribers');
    const nav = await screen.findByRole('navigation', { name: 'Operations' });
    fireEvent.click(within(nav).getByRole('link', { name: /Product catalog/ }));
    expect(window.location.pathname).toBe('/catalog');
    expect(await screen.findByRole('heading', { name: 'Product catalog' })).toBeTruthy();
    window.history.back();
    expect(await screen.findByRole('heading', { name: 'Subscribers' })).toBeTruthy();
  });

  it('leaves modified clicks to the browser', async () => {
    backend();
    start('/subscribers');
    const nav = await screen.findByRole('navigation', { name: 'Operations' });
    fireEvent.click(within(nav).getByRole('link', { name: /Product catalog/ }), { ctrlKey: true });
    expect(window.location.pathname).toBe('/subscribers');
  });

  it('answers an unknown path with a not-found page', async () => {
    backend();
    start('/billing');
    expect(await screen.findByText('Page not found')).toBeTruthy();
    expect(screen.getByText('Nothing lives at /billing.')).toBeTruthy();
  });
});

describe('shell', () => {
  it('shows the business date and time zone from /internal/v1/context', async () => {
    backend();
    start('/subscribers');
    const bar = await screen.findByRole('banner');
    expect(await within(bar).findByText('2026-10-01')).toBeTruthy();
    expect(within(bar).getByText('UTC+3')).toBeTruthy();
  });

  it('has the environment tag, operator and counts, Billing runs included', async () => {
    backend();
    start('/subscribers');
    const nav = await screen.findByRole('navigation', { name: 'Operations' });
    expectUi('ui.shell');
    expect(screen.getByText('PROD')).toBeTruthy();
    expect(screen.getAllByText('i.petrova').length).toBeGreaterThan(0);
    const links = within(nav).getAllByRole('link');
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/numbers',
      '/subscribers',
      '/catalog',
      '/billing-runs',
      '/financial-accounts',
    ]);
    expect(await within(nav).findByText('1,284')).toBeTruthy();
    expect(await within(nav).findByText('12')).toBeTruthy();
    const financial = within(nav).getByRole('link', { name: /Financial accounts/ });
    expect(await within(financial).findByText('23')).toBeTruthy();
    const runs = within(nav).getByRole('link', { name: /Billing runs/ });
    expect(await within(runs).findByText('9')).toBeTruthy(); // X-Total-Count of the runs
    expect(within(runs).queryByText('failed')).toBeNull();
    expect(
      within(nav)
        .getByRole('link', { name: /Subscribers/ })
        .getAttribute('aria-current'),
    ).toBe('page');
  });

  it('shows the red "failed" badge instead of the count when the latest run failed', async () => {
    backend(undefined, () =>
      json([{ ...RUN, status: 'failed', failureKind: 'run' }], { total: 9 }),
    );
    start('/subscribers');
    const nav = await screen.findByRole('navigation', { name: 'Operations' });
    const runs = within(nav).getByRole('link', { name: /Billing runs/ });
    expect((await within(runs).findByText('failed')).className).toBe('badge');
    expect(within(runs).queryByText('9')).toBeNull();
  });

  it('opens Billing runs from the navigation', async () => {
    backend();
    start('/subscribers');
    const nav = await screen.findByRole('navigation', { name: 'Operations' });
    fireEvent.click(within(nav).getByRole('link', { name: /Billing runs/ }));
    expect(window.location.pathname).toBe('/billing-runs');
    expect(await screen.findByRole('heading', { name: 'Billing runs' })).toBeTruthy();
    expect(await screen.findByText('No runs yet')).toBeTruthy();
    expect(
      within(nav)
        .getByRole('link', { name: /Billing runs/ })
        .getAttribute('aria-current'),
    ).toBe('page');
  });

  it('keeps Subscribers active on a subscriber card, with breadcrumbs back', async () => {
    backend();
    start(`/subscribers/${ID}`);
    const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumbs' });
    // the screen sets its crumbs in an effect, one render after the bar exists
    const back = await within(crumbs).findByRole('link', { name: 'Subscribers' });
    expect(back.getAttribute('href')).toBe('/subscribers');
    expect(within(crumbs).getByText('BA-1F3A9C2B')).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Operations' });
    expect(
      within(nav)
        .getByRole('link', { name: /Subscribers/ })
        .getAttribute('aria-current'),
    ).toBe('page');
  });

  it('shows the authenticated operator even without a context operator', async () => {
    backend(() => json({ ...CONTEXT, operator: null, environment: 'dev' }));
    start('/subscribers');
    expect(await screen.findByText('i.petrova')).toBeTruthy();
    expect(screen.getByText('DEV')).toBeTruthy();
  });

  it('shows the failing context call and recovers on Retry', async () => {
    let fail = true;
    backend(() => (fail ? tmfError(503, 'Service Unavailable') : json(CONTEXT)));
    start('/subscribers');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load the business date')).toBeTruthy();
    expect(
      within(alert).getByText('GET /internal/v1/context → 503 Service Unavailable'),
    ).toBeTruthy();
    fail = false;
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('2026-10-01')).toBeTruthy();
    // the screen behind it loads and settles (its empty state): nothing failed
    expect(await screen.findByText(/No subscribers/)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
