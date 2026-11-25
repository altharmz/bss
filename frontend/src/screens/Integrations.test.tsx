import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { EVENT_TYPES, HUB_APIS, hubBody } from '../api/hubs';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

const LIST = 'GET /internal/v1/eventSubscription?offset=0&limit=100';
const H1 = 'e0000001-0000-4000-8000-000000000001';
const H2 = 'e0000002-0000-4000-8000-000000000002';
const PAYMENT = '/tmf-api/payment/v4';
const CATALOG = '/tmf-api/productCatalogManagement/v5';

const HUB_PAY = {
  id: H1,
  api: { id: 'TMF676', name: 'Payment Management' },
  hub: `${PAYMENT}/hub/${H1}`,
  callback: 'https://example.org/pay',
  query: 'eventType=PaymentCreateEvent',
  eventTypes: ['PaymentCreateEvent'],
  subscribedAt: '2026-10-01T09:00:00Z',
  lastDelivery: { state: 'failed', at: '2026-10-01T09:30:00Z' },
  failures: 3,
};
const HUB_CAT = {
  ...HUB_PAY,
  id: H2,
  api: { id: 'TMF620', name: 'Product Catalog Management' },
  hub: `${CATALOG}/hub/${H2}`,
  callback: 'https://example.org/cat',
  query: null,
  eventTypes: [],
  lastDelivery: null,
  failures: 0,
};
const STATUS = { date: '2026-10-01', status: 'ok', ageSeconds: 3600 };

function backend(routes: Record<string, Handler> = {}, role = 'admin') {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role, mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    [LIST]: () => json([HUB_PAY, HUB_CAT], { total: 2 }),
    'GET /billing-status': () => json(STATUS),
    'GET /healthz': () => json({ status: 'ok', db: 'up' }),
    ...routes,
  });
}

function open() {
  window.history.replaceState(null, '', '/integrations');
  render(<App />);
}

const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method !== undefined && init.method !== 'GET')
    .map(([url, init]) => [init?.method, url, init?.body ?? null]);

describe('Integrations', () => {
  it('lists the hubs, with the delivery summary, and the System panel', async () => {
    backend();
    open();
    await screen.findByText('https://example.org/pay');
    expectUi('ui.integrations');
    expectUi('ui.integrations.list');
    expect(screen.getByRole('button', { name: 'New subscription' })).toBe(
      expectUi('ui.integrations.new'),
    );
    expect(
      expectUi('ui.integrations.delete')
        .getAttribute('aria-label')
        ?.startsWith('Delete subscription '),
    ).toBe(true);
    expect(screen.getByText('All events')).toBeTruthy();
    expect(screen.getByText('failed')).toBeTruthy();
    expect(screen.getByText('None yet')).toBeTruthy();
    const system = await screen.findByText('2026-10-01', { selector: 'dd span' });
    expect(system).toBeTruthy();
    expectUi('ui.system');
    expectUi('ui.system.health');
    expectUi('ui.system.billingStatus');
    const status = within(screen.getByText('Billing status').parentElement as HTMLElement);
    expect(status.getByText('Last run')).toBeTruthy();
    expect(status.getByText('1 h ago').getAttribute('title')).toBe('ageSeconds');
    expect(status.getByText('Business date')).toBeTruthy();
    expect(status.queryByText('ageSeconds:')).toBeNull();
    expect(screen.getAllByText('OK')).toHaveLength(2); // health and billing status
    const link = expectUi('ui.system.schema');
    expect(link.getAttribute('href')).toBe('/tmf-api/schema/BssBillingAccount.schema.json');
    expect(link.getAttribute('target')).toBe('_blank');
    // System section: Integrations, and Users for an admin
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Integrations' })).toBeTruthy();
    expect(within(nav).getByRole('link', { name: 'Users' })).toBeTruthy();
  });

  it('shows an unhealthy service and an overdue billing status as they are', async () => {
    backend({
      'GET /healthz': () => json({ status: 'degraded', db: 'down' }, { status: 503 }),
      'GET /billing-status': () =>
        json({ date: '2026-09-30', status: 'stale', ageSeconds: 99999 }, { status: 503 }),
    });
    open();
    await screen.findByText(/HTTP 503 \{"status":"degraded","db":"down"\}/);
    expect(screen.getByText('Stale')).toBeTruthy();
    expect(screen.getByText('27 h ago')).toBeTruthy();
    expect(screen.getByText('HTTP 503')).toBeTruthy();
  });

  // TEST-CHANGE (#467): System, Integrations with it, is admin-only; the hub endpoints refuse a user
  it('refuses Integrations to a non-admin and hides it', async () => {
    const { calls } = backend({}, 'user');
    open();
    await screen.findByRole('heading', { name: 'Not allowed' });
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).queryByRole('link', { name: 'Users' })).toBeNull();
    expect(within(nav).queryByRole('link', { name: 'Integrations' })).toBeNull();
    expect(calls.some((call) => call.includes('eventSubscription'))).toBe(false);
  });

  it('creates a v4 hub with a choice of events and a v5 hub for every event', async () => {
    const { fetchMock } = backend({
      [`POST ${PAYMENT}/hub`]: () => json({ id: H1 }, { status: 201 }),
      [`POST ${CATALOG}/hub`]: () => json({ id: H2 }, { status: 201 }),
    });
    open();
    await screen.findByText('https://example.org/pay');
    fireEvent.click(screen.getByRole('button', { name: 'New subscription' }));
    const dialog = screen.getByRole('dialog', { name: 'New subscription' });
    expectUi('ui.integrations.form');
    expectUi('ui.integrations.events');
    fireEvent.change(within(dialog).getByLabelText('API'), { target: { value: 'TMF676' } });
    fireEvent.change(within(dialog).getByLabelText('Callback URL'), {
      target: { value: 'https://example.org/events' },
    });
    fireEvent.click(within(dialog).getByLabelText('RefundCreateEvent'));
    fireEvent.click(within(dialog).getByLabelText('PaymentCreateEvent'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await screen.findByText('Subscription created');

    fireEvent.click(screen.getByRole('button', { name: 'New subscription' }));
    const second = screen.getByRole('dialog', { name: 'New subscription' });
    fireEvent.change(within(second).getByLabelText('Callback URL'), {
      target: { value: 'https://example.org/all' },
    });
    fireEvent.click(within(second).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(writes(fetchMock)).toHaveLength(2));
    expect(writes(fetchMock)).toEqual([
      [
        'POST',
        `${PAYMENT}/hub`,
        JSON.stringify({
          callback: 'https://example.org/events',
          query: 'eventType=RefundCreateEvent,PaymentCreateEvent',
        }),
      ],
      ['POST', `${CATALOG}/hub`, JSON.stringify({ callback: 'https://example.org/all' })],
    ]);
  });

  it('keeps the form and shows the server refusal for a non-public callback', async () => {
    backend({
      [`POST ${CATALOG}/hub`]: () => tmfError(422, 'callback must lead to a public address'),
    });
    open();
    await screen.findByText('https://example.org/pay');
    fireEvent.click(screen.getByRole('button', { name: 'New subscription' }));
    const dialog = screen.getByRole('dialog', { name: 'New subscription' });
    fireEvent.change(within(dialog).getByLabelText('Callback URL'), {
      target: { value: 'http://10.0.0.1/x' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    expect(await within(dialog).findByText(/callback must lead to a public address/)).toBeTruthy();
    expect((within(dialog).getByLabelText('Callback URL') as HTMLInputElement).value).toBe(
      'http://10.0.0.1/x',
    );
  });

  it('deletes through the API that owns the hub, after a confirmation', async () => {
    const { fetchMock } = backend({
      [`DELETE ${PAYMENT}/hub/${H1}`]: () => new Response(null, { status: 204 }),
    });
    open();
    await screen.findByText('https://example.org/pay');
    fireEvent.click(screen.getByRole('button', { name: `Delete subscription ${H1}` }));
    const dialog = screen.getByRole('dialog', { name: 'Delete subscription' });
    expectUi('ui.integrations.confirmDelete');
    expect(writes(fetchMock)).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await screen.findByText('Subscription deleted');
    expect(writes(fetchMock)).toEqual([['DELETE', `${PAYMENT}/hub/${H1}`, null]]);
  });

  it('builds the body for none, one and several events', () => {
    expect(hubBody('https://x.test/a', [])).toBe('{"callback":"https://x.test/a"}');
    expect(hubBody('https://x.test/a', ['A', 'B'])).toBe(
      '{"callback":"https://x.test/a","query":"eventType=A,B"}',
    );
    expect(HUB_APIS.map((a) => a.id)).toEqual(Object.keys(EVENT_TYPES));
  });
});
