import { request } from '../api/client';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, pending, stubFetch, tmfError, type Handler } from '../test/fetch';
import { RUN } from '../test/fixtures';
import { App } from './App';
import { parseRoute } from './router';

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
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    'GET /internal/v1/context': context,
    [NAV_RUNS]: latestRun,
    // the billing runs screen: no runs (the screen has its own tests)
    'GET /internal/v1/billingRun?offset=0&limit=20': empty,
    // the billing account list: no accounts
    'GET /internal/v1/subscriber?offset=0&limit=50': empty,
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
    ['POST', '/tmf-api/accountManagement/v5/billingAccount'],
    ['DELETE', '/tmf-api/accountManagement/v5/billingAccount/a1'],
    ['POST', '/tmf-api/productOrderingManagement/v5/productOrder'],
    ['POST', '/tmf-api/productCatalogManagement/v5/productOffering'],
    ['DELETE', '/tmf-api/productCatalogManagement/v5/productOffering/o1'],
  ])('sends no sidebar count request, also after %s %s', async (method, path) => {
    const { calls, fetchMock } = backend();
    start('/billing-accounts');
    await screen.findByRole('navigation', { name: 'Main' });
    fetchMock.mockImplementationOnce(async () => json({ id: ID }, { status: 201 }));
    await act(async () => {
      await request(method, path);
    });
    await screen.findByRole('navigation', { name: 'Main' });
    // an unstubbed request fails the test (setup.ts); the three count reads are named too
    expect(calls.filter((call) => call.includes('limit=1&fields=id'))).toEqual([]);
  });
  it('marks the active sidebar item and puts operator identity in the footer trigger', async () => {
    backend();
    start('/billing-accounts');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const active = within(nav).getByRole('link', { name: /Billing accounts/ });
    expect(active.classList.contains('is-active')).toBe(true);
    expect(active.getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Parties' }).classList.contains('is-active')).toBe(
      false,
    );
    const trigger = expectUi('ui.shell.operator');
    expect(trigger.tagName).toBe('SUMMARY');
    expect(within(trigger).getByText('i.petrova')).toBeTruthy();
    expect(within(trigger).getByText('admin')).toBeTruthy();
    expect(within(trigger).getByText('I.')).toBeTruthy();
    expect(screen.getByText('BSS Admin')).toBeTruthy();
    // the title names the item's TMF API
    expect(active.getAttribute('title')).toBe('TMF666 billingAccount');
    expect(within(nav).getByRole('link', { name: 'Billing runs' }).getAttribute('title')).toBe(
      'internal',
    );
  });
  it.each([
    ['/billing-accounts', { name: 'subscribers' }],
    [`/billing-accounts/${ID}`, { name: 'subscriber', accountId: ID }],
    [`/billing-accounts/${ID.toUpperCase()}`, { name: 'subscriber', accountId: ID }],
    // the old paths still parse; currentPath replaces them in the address bar
    ['/subscribers', { name: 'subscribers' }],
    [`/subscribers/${ID}`, { name: 'subscriber', accountId: ID }],
    ['/customers', { name: 'customers' }],
    ['/catalog', { name: 'catalog' }],
    ['/billing-runs', { name: 'billingRuns' }],
    ['/financial-accounts', { name: 'financialAccounts' }],
    ['/parties', { name: 'parties' }],
    ['/billing-settings', { name: 'billingSettings' }],
    ['/settings/logging', { name: 'logging' }],
    ['/bills', { name: 'bills' }],
    ['/wholesale', { name: 'wholesale' }],
    [`/wholesale/${ID.toUpperCase()}`, { name: 'wholesale', invoiceId: ID }],
    ['/payments', { name: 'payments' }],
    ['/dunning', { name: 'dunning' }],
    ['/dunning/', { name: 'dunning' }],
    ['/balances', { name: 'balances' }],
    ['/reports', { name: 'reports' }],
    ['/prepaid-lifecycle', { name: 'prepaidLifecycle' }],
    ['/prepaid-lifecycle/', { name: 'prepaidLifecycle' }],
    ['/orders', { name: 'orders' }],
    ['/products', { name: 'products' }],
    ['/billing', { name: 'notFound', path: '/billing' }], // not /billing: /billing-status
    ['/subscribers/nope', { name: 'notFound', path: '/subscribers/nope' }],
    ['/billing-accounts/nope', { name: 'notFound', path: '/billing-accounts/nope' }],
  ])('%s', (path, route) => {
    expect(parseRoute(path)).toEqual(route);
  });

  it('sends / to the billing account list', async () => {
    backend();
    start('/');
    expect(window.location.pathname).toBe('/billing-accounts');
    expect(await screen.findByRole('heading', { name: 'Billing accounts' })).toBeTruthy();
  });

  it.each([
    ['/subscribers', '/billing-accounts'],
    [`/subscribers/${ID}`, `/billing-accounts/${ID}`],
    [`/subscribers/${ID}?tab=bills#usage`, `/billing-accounts/${ID}?tab=bills#usage`],
  ])('replaces the old %s with %s, without a history entry', async (from, to) => {
    backend();
    window.history.replaceState(null, '', '/catalog');
    window.history.pushState(null, '', from);
    const before = window.history.length;
    render(<App />);
    await screen.findByRole('navigation', { name: 'Main' });
    expect(`${window.location.pathname}${window.location.search}${window.location.hash}`).toBe(to);
    expect(window.history.length).toBe(before);
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(
      within(nav)
        .getByRole('link', { name: /Billing accounts/ })
        .getAttribute('aria-current'),
    ).toBe('page');
  });

  it('navigates in place on a nav click and follows the back button', async () => {
    backend();
    start('/billing-accounts');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    fireEvent.click(within(nav).getByRole('link', { name: /Product catalog/ }));
    expect(window.location.pathname).toBe('/catalog');
    expect(await screen.findByRole('heading', { name: 'Product catalog' })).toBeTruthy();
    window.history.back();
    expect(await screen.findByRole('heading', { name: 'Billing accounts' })).toBeTruthy();
  });

  it('leaves modified clicks to the browser', async () => {
    backend();
    start('/billing-accounts');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    fireEvent.click(within(nav).getByRole('link', { name: /Product catalog/ }), { ctrlKey: true });
    expect(window.location.pathname).toBe('/billing-accounts');
  });

  it('answers an unknown path with a not-found page', async () => {
    backend();
    start('/billing');
    expect(await screen.findByText('Page not found')).toBeTruthy();
    expect(screen.getByText(/Nothing lives at \/billing\./)).toBeTruthy();
    expectUi('ui.notFound');
  });
});

describe('shell', () => {
  it('shows the business date and time zone from /internal/v1/context', async () => {
    backend();
    start('/billing-accounts');
    const bar = await screen.findByRole('banner');
    expect(await within(bar).findByText('2026-10-01')).toBeTruthy();
    expect(within(bar).getByText('UTC+3')).toBeTruthy();
  });

  it('has the environment tag, operator and the items without counts', async () => {
    const { calls } = backend();
    start('/billing-accounts');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    expectUi('ui.shell');
    expect(screen.getByText('PROD')).toBeTruthy();
    expect(screen.getAllByText('i.petrova').length).toBeGreaterThan(0);
    const links = within(nav).getAllByRole('link');
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/parties',
      '/financial-accounts',
      '/customers',
      '/billing-accounts',
      '/products',
      '/orders',
      '/signups',
      '/catalog',
      '/numbers',
      '/sims',
      '/ports',
      '/billing-runs',
      '/bills',
      '/wholesale',
      '/payments',
      '/dunning',
      '/billing-settings',
      '/balances',
      '/prepaid-commands',
      '/prepaid-lifecycle',
      '/reports',
      '/integrations',
      '/users',
      '/settings/logging',
    ]);
    // TEST-CHANGE (#457): seven labelled sections in TMF / ODA domain order (#414), Customers
    // down the account hierarchy and Catalog alone, each item under its label
    const labels = Array.from(nav.children).map((el) =>
      el.classList.contains('section') ? `# ${el.textContent}` : (el.getAttribute('href') ?? ''),
    );
    expect(labels).toEqual([
      '# Customers',
      '/parties',
      '/financial-accounts',
      '/customers',
      '/billing-accounts',
      '/products',
      '/orders',
      '/signups',
      '# Catalog',
      '/catalog',
      '# Resources',
      '/numbers',
      '/sims',
      '/ports',
      '# Billing',
      '/billing-runs',
      '/bills',
      '/wholesale',
      '/payments',
      '/dunning',
      '/billing-settings',
      '# Prepaid',
      '/balances',
      '/prepaid-commands',
      '/prepaid-lifecycle',
      '# Reports',
      '/reports',
      '# System',
      '/integrations',
      '/users',
      '/settings/logging',
    ]);
    for (const section of [
      'customers',
      'catalog',
      'resources',
      'billing',
      'prepaid',
      'reports',
      'system',
    ]) {
      expect(nav.contains(expectUi(`ui.shell.section.${section}`))).toBe(true);
    }
    expectUi('ui.shell.theme');
    // no item carries a count: the link text is the label alone
    for (const label of ['Billing accounts', 'Product catalog', 'Orders']) {
      expect(within(nav).getByRole('link', { name: new RegExp(`^${label}`) }).textContent).toBe(
        label,
      );
    }
    // Financial accounts is a read-only aggregate: no count, and no request for one
    const financial = within(nav).getByRole('link', { name: /^Financial accounts/ });
    expect(financial.textContent).toBe('Financial accounts');
    expect(calls.some((c) => c.includes('financialAccount'))).toBe(false);
    const runs = within(nav).getByRole('link', { name: /Billing runs/ });
    await waitFor(() => expect(calls).toContain(NAV_RUNS));
    expect(runs.textContent).toBe('Billing runs');
    expect(within(runs).queryByText('failed')).toBeNull();
    expect(
      within(nav)
        .getByRole('link', { name: /Billing accounts/ })
        .getAttribute('aria-current'),
    ).toBe('page');
  });

  it('shows the red "failed" badge when the latest run failed', async () => {
    backend(undefined, () =>
      json([{ ...RUN, status: 'failed', failureKind: 'run' }], { total: 9 }),
    );
    start('/billing-accounts');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const runs = within(nav).getByRole('link', { name: /Billing runs/ });
    expect((await within(runs).findByText('failed')).className).toBe('bss-badge badge-failed');
    expect(runs.textContent).toBe('Billing runsfailed');
  });

  it('opens Billing runs from the navigation', async () => {
    backend();
    start('/billing-accounts');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
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

  it('keeps Billing accounts active on a card, with breadcrumbs back', async () => {
    backend();
    start(`/billing-accounts/${ID}`);
    const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumbs' });
    // the screen sets its crumbs in an effect, one render after the bar exists
    const back = await within(crumbs).findByRole('link', { name: 'Billing accounts' });
    expect(back.getAttribute('href')).toBe('/billing-accounts');
    expect(within(crumbs).getByText('BA-1F3A9C2B')).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(
      within(nav)
        .getByRole('link', { name: /Billing accounts/ })
        .getAttribute('aria-current'),
    ).toBe('page');
  });

  it('shows the authenticated operator even without a context operator', async () => {
    backend(() => json({ ...CONTEXT, operator: null, environment: 'dev' }));
    start('/billing-accounts');
    expect(await screen.findByText('i.petrova')).toBeTruthy();
    expect(screen.getByText('DEV')).toBeTruthy();
  });

  it('shows the failing context call and recovers on Retry', async () => {
    let fail = true;
    backend(() => (fail ? tmfError(503, 'Service Unavailable') : json(CONTEXT)));
    start('/billing-accounts');
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load the business date')).toBeTruthy();
    expect(
      within(alert).getByText('GET /internal/v1/context → 503 Service Unavailable'),
    ).toBeTruthy();
    fail = false;
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('2026-10-01')).toBeTruthy();
    // the screen behind it loads and settles (its empty state): nothing failed
    expect(await screen.findByText(/No billing accounts/)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
