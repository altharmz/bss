import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, tmfCodeError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { C, P1, P2, P3, backend, open } from '../test/subscriberCardKit';

// The Security tab (issue 226): the account PIN status with Set/Reset, the line locks with
// the PIN asked for clearing one, and the read-only audit log. Write requests are replayed
// against the real API in tests/care/test_account_security.py
// (test_security_requests_as_the_ui_sends_them).

const SEC = '/internal/v1/security';
const PIN = `GET ${SEC}/pin/${C}`;
const AUDIT = `GET ${SEC}/audit?customer.id=${C}&offset=0&limit=25`;
const lockOf = (id: string, over: Record<string, unknown> = {}) => ({
  product: { id, href: `/tmf-api/productInventory/v5/product/${id}` },
  simSwap: false,
  portOut: false,
  updatedAt: null,
  updatedBy: null,
  reason: null,
  ...over,
});
const PIN_SET = {
  customer: { id: C, href: `/tmf-api/customer/v5/customer/${C}` },
  set: true,
  setAt: '2026-10-01T07:00:00Z',
  setBy: 'i.petrova',
  reason: 'identity checked at the counter',
  failedAttempts: 2,
  lockedUntil: '2026-10-01T08:30:00Z',
};
const NO_PIN = {
  ...PIN_SET,
  set: false,
  setAt: null,
  setBy: null,
  reason: null,
  failedAttempts: 0,
  lockedUntil: null,
};
const entry = (id: string, over: Record<string, unknown>) => ({
  id,
  at: '2026-10-01T07:00:00Z',
  operator: 'i.petrova',
  role: 'admin',
  customer: { id: C, href: '' },
  billingAccount: null,
  product: null,
  action: 'pin_set',
  outcome: 'done',
  reason: 'identity checked at the counter',
  verification: 'none',
  detail: {},
  ...over,
});
const E1 = entry('a0000001-0000-4000-8000-000000000001', {});
const E2 = entry('a0000002-0000-4000-8000-000000000002', {
  at: '2026-10-01T08:00:00Z',
  action: 'sim_swap',
  outcome: 'refused',
  reason: 'pinInvalid',
  verification: 'none',
  product: { id: P1, href: '' },
});

function tab(routes: Record<string, Handler> = {}) {
  return backend({
    [PIN]: () => json(PIN_SET),
    [AUDIT]: () => json([E2, E1]),
    [`GET ${SEC}/lock/${P1}`]: () => json(lockOf(P1, { simSwap: true })),
    [`GET ${SEC}/lock/${P2}`]: () => json(lockOf(P2)),
    [`GET ${SEC}/lock/${P3}`]: () => json(lockOf(P3)),
    ...routes,
  });
}

/** The n-th line's Change locks button (rows in product order). */
function changeLocks(locks: HTMLElement, n: number): HTMLElement {
  const button = within(locks).getAllByRole('button', { name: 'Change locks' })[n];
  if (button === undefined) throw new Error(`no line ${n}`);
  return button;
}

function puts(fetchMock: ReturnType<typeof backend>['fetchMock']) {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'PUT')
    .map(([url, init]) => [
      String(url),
      String(init?.body),
      (init?.headers as Record<string, string>)['X-Account-Pin'],
    ]);
}

describe('security tab', () => {
  it('is loaded only when opened', async () => {
    const { calls } = backend();
    await open();
    expect(screen.getByRole('tab', { name: /^Security/ })).toBeTruthy();
    expect(calls.some((c) => c.includes('/security/'))).toBe(false);
  });

  it('shows the PIN status, never the PIN, with the lockout end', async () => {
    tab();
    await open('#security');
    const pin = await waitFor(() => expectUi('ui.subscriber.security.pin'));
    await within(pin).findByText('Set');
    expect(within(pin).getByText('2026-10-01 10:00:00 by i.petrova')).toBeTruthy();
    expect(within(pin).getByText('identity checked at the counter')).toBeTruthy();
    expect(within(pin).getByText('2')).toBeTruthy();
    expect(within(pin).getByText('2026-10-01 11:30:00')).toBeTruthy();
    expectUi('ui.subscriber.security');
  });

  it('sets a PIN with a reason, checking both, and reloads the audit log', async () => {
    let audits = 0;
    const { fetchMock } = tab({
      [PIN]: () => json(NO_PIN),
      [AUDIT]: () => {
        audits += 1;
        return json(audits === 1 ? [] : [E1]);
      },
      [`PUT ${SEC}/pin/${C}`]: () => json({ ...PIN_SET, lockedUntil: null }),
    });
    await open('#security');
    await screen.findByText('Not set');
    await screen.findByText('No entries');
    fireEvent.click(expectUi('ui.subscriber.security.setPin'));
    const dialog = await waitFor(() => expectUi('ui.subscriber.security.pinDialog'));
    fireEvent.change(within(dialog).getByLabelText('New PIN'), { target: { value: '12a45' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), { target: { value: 'ok' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set PIN' }));
    expect(within(dialog).getByText('Enter 6 to 8 digits')).toBeTruthy();
    expect(within(dialog).getByText('Enter the reason (at least 3 characters)')).toBeTruthy();
    expect(puts(fetchMock)).toEqual([]);
    fireEvent.change(within(dialog).getByLabelText('New PIN'), { target: { value: '482913' } });
    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: ' identity checked at the counter ' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set PIN' }));
    await screen.findByText('Account PIN set');
    expect(puts(fetchMock)).toEqual([
      [`${SEC}/pin/${C}`, '{"pin":"482913","reason":"identity checked at the counter"}', undefined],
    ]);
    await waitFor(() => expect(audits).toBe(2));
    expect(screen.queryByText('482913')).toBeNull();
  });

  it('lists the locks of every live line and clears one with the account PIN', async () => {
    const { fetchMock } = tab({
      [`PUT ${SEC}/lock/${P1}`]: () =>
        json(
          lockOf(P1, {
            updatedAt: '2026-10-01T09:00:00Z',
            updatedBy: 'i.petrova',
            reason: 'customer called',
          }),
        ),
    });
    await open('#security');
    const locks = await waitFor(() => expectUi('ui.subscriber.security.locks'));
    await within(locks).findByText('Locked');
    expect(within(locks).getAllByRole('button', { name: 'Change locks' })).toHaveLength(3);
    fireEvent.click(changeLocks(locks, 0));
    const dialog = await waitFor(() => expectUi('ui.subscriber.security.lockDialog'));
    // no PIN is asked for until a lock is being cleared
    expect(within(dialog).queryByLabelText('Account PIN')).toBeNull();
    fireEvent.click(within(dialog).getByLabelText(/SIM swap lock/));
    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: 'customer called' },
    });
    fireEvent.change(within(dialog).getByLabelText('Account PIN'), {
      target: { value: '482913' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save locks' }));
    await screen.findByText('Line lock cleared');
    expect(puts(fetchMock)).toEqual([
      [
        `${SEC}/lock/${P1}`,
        '{"simSwap":false,"portOut":false,"reason":"customer called"}',
        '482913',
      ],
    ]);
  });

  it('sets a lock without a PIN and explains a PIN refusal of a clearing', async () => {
    const { fetchMock } = tab({
      [`PUT ${SEC}/lock/${P2}`]: () => json(lockOf(P2, { portOut: true })),
      [`PUT ${SEC}/lock/${P1}`]: () =>
        tmfCodeError(403, 'pinLocked', 'too many wrong PINs: verification is locked'),
    });
    await open('#security');
    const locks = await waitFor(() => expectUi('ui.subscriber.security.locks'));
    await within(locks).findByText('Locked');
    fireEvent.click(changeLocks(locks, 1));
    let dialog = await waitFor(() => expectUi('ui.subscriber.security.lockDialog'));
    fireEvent.click(within(dialog).getByLabelText(/Port-out lock/));
    expect(within(dialog).queryByLabelText('Account PIN')).toBeNull();
    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: 'fraud report' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save locks' }));
    await screen.findByText('Line locks saved');
    fireEvent.click(changeLocks(locks, 0));
    dialog = await waitFor(() => expectUi('ui.subscriber.security.lockDialog'));
    fireEvent.click(within(dialog).getByLabelText(/SIM swap lock/));
    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: 'customer called' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save locks' }));
    await within(dialog).findByText(/Too many wrong account PINs/);
    expect(
      (within(dialog).getByRole('button', { name: 'Save locks' }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(puts(fetchMock)).toEqual([
      [`${SEC}/lock/${P2}`, '{"simSwap":false,"portOut":true,"reason":"fraud report"}', undefined],
      [
        `${SEC}/lock/${P1}`,
        '{"simSwap":false,"portOut":false,"reason":"customer called"}',
        undefined,
      ],
    ]);
  });

  it('shows the audit log read only, filters it and pages it', async () => {
    const filtered = `GET ${SEC}/audit?customer.id=${C}&action=sim_swap&outcome=refused&offset=0&limit=25`;
    const second = `GET ${SEC}/audit?customer.id=${C}&action=sim_swap&outcome=refused&offset=25&limit=25`;
    const { calls } = tab({
      [AUDIT]: () => json([E2, E1], { more: false }),
      [`GET ${SEC}/audit?customer.id=${C}&action=sim_swap&offset=0&limit=25`]: () => json([E2]),
      [filtered]: () => json([E2], { more: true }),
      [second]: () => json([]),
    });
    await open('#security');
    const audit = await waitFor(() => expectUi('ui.security.audit'));
    await within(audit).findByText('pinInvalid');
    const rows = within(audit)
      .getAllByRole('row')
      .map((r) => r.textContent ?? '');
    const refused = rows.find((t) => t.includes('SIM swap')) ?? '';
    expect(refused).toContain('2026-10-01 11:00:00');
    expect(refused).toContain('Refused pinInvalid');
    expect(refused).toContain('i.petrova');
    const set = rows.find((t) => t.includes('PIN set')) ?? '';
    expect(set).toContain('identity checked at the counter');
    expect(set).toContain('Done');
    fireEvent.change(expectUi('ui.security.audit.action'), { target: { value: 'sim_swap' } });
    fireEvent.change(expectUi('ui.security.audit.outcome'), { target: { value: 'refused' } });
    await waitFor(() => expect(calls).toContain(filtered));
    fireEvent.click(await waitFor(() => expectUi('ui.security.audit.next')));
    await waitFor(() => expect(calls).toContain(second));
    await screen.findByText('No matches');
    expect(within(audit).queryByRole('button', { name: /delete|edit/i })).toBeNull();
  });
});
