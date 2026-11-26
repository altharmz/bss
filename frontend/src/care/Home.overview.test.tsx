import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { components } from '../api/generated/care';
import { json, stubFetch, tmfError } from '../test/fetch';
import { AddOns } from './AddOns';
import { Home } from './Home';

type S = components['schemas'];
const ACCOUNT = '11111111-0000-4000-8000-000000000001';
const LINE = '22222222-0000-4000-8000-000000000002';
const PASS = '33333333-0000-4000-8000-000000000003';

function account(autoTopUp: boolean): S['CareAccount'] {
  return {
    id: ACCOUNT,
    name: 'Family prepaid',
    ratingType: 'prepaid',
    state: 'Active',
    currency: 'USD',
    balance: { spendable: { unit: 'USD', value: 12.5 }, held: { unit: 'USD', value: 0 } },
    autoTopUp: autoTopUp
      ? {
          enabled: true,
          threshold: { unit: 'USD', value: 5 },
          amount: { unit: 'USD', value: 20 },
          defaultPaymentMethod: null,
        }
      : null,
    serviceAddress: null,
  };
}

const plan: S['CareLine'] = {
  id: LINE,
  billingAccount: { id: ACCOUNT },
  number: '15125550100',
  plan: { id: 'p1', name: 'Prepaid M' },
  offeringKind: 'plan',
  status: 'active',
  startDate: '2026-11-01',
  paidThrough: '2026-12-01',
  renewalMode: 'balanceThenCard',
  addOns: [{ id: 'a1', name: 'Roaming pack', endDate: null }],
};
const payAsYouGo: S['CareLine'] = {
  ...plan,
  id: PASS,
  number: null,
  plan: { id: 'p2', name: 'Pay as you go' },
  offeringKind: 'payAsYouGo',
  paidThrough: null,
  addOns: [],
};

function allowance(
  kind: 'data' | 'voice',
  used: number,
  percent: number | null,
): S['CareUsage']['allowances'][number] {
  return {
    kind,
    zone: 'domestic',
    unit: kind === 'data' ? 'bytes' : 'seconds',
    used,
    included: 1000,
    remaining: Math.max(0, 1000 - used),
    percent,
    thresholds: [],
    pooled: false,
    pool: null,
  };
}

function usage(id: string, allowances: S['CareUsage']['allowances']): S['CareUsage'] {
  return {
    line: { id },
    date: '2026-11-26',
    period: null,
    allowances,
    throttled: false,
    payAsYouGo: null,
  };
}

describe('self-care home', () => {
  it("shows each account's balance and each line with its usage and add-ons", async () => {
    stubFetch({
      'GET /self-care/v1/account': () => json([account(false)]),
      'GET /self-care/v1/line': () => json([plan, payAsYouGo]),
      [`GET /self-care/v1/line/${LINE}/usage`]: () =>
        json(usage(LINE, [allowance('data', 1200, 120), allowance('voice', 0, null)])),
      [`GET /self-care/v1/line/${PASS}/usage`]: () => json(usage(PASS, [])),
      'GET /self-care/v1/paymentMethod': () => json([]),
      [`GET /self-care/v1/offering?kind=addOn&billingAccountId=${ACCOUNT}`]: () => json([]),
    });
    render(<Home />);
    expect(screen.getByRole('status').textContent).toBe('Loading your lines and balance…');
    const section = (await screen.findByRole('heading', { name: 'Family prepaid' }))
      .parentElement as HTMLElement;
    expect(section.textContent).toContain('Available balance 12.50 USD');

    const [first, second] = screen.getAllByRole('article');
    const line = within(first as HTMLElement);
    expect(line.getByRole('heading', { name: '15125550100' })).toBeTruthy();
    expect(line.getByText('Prepaid M · active')).toBeTruthy();
    expect(line.getByText('Renewal date 2026-12-01')).toBeTruthy();
    expect(line.getByText('Renewal mode: balanceThenCard')).toBeTruthy();
    expect(line.getByRole('listitem').textContent).toBe('Roaming pack');
    // an overused allowance fills its bar, never beyond it; no percent, no bar
    const bars = (first as HTMLElement).querySelectorAll('progress');
    expect([...bars].map((bar) => bar.getAttribute('value'))).toEqual(['100']);
    expect(line.getByText(/domestic data · 1200 of 1000 bytes/)).toBeTruthy();
    expect(line.getByText(/domestic voice · 0 of 1000 seconds/)).toBeTruthy();
    expect(await line.findByText('No add-ons available to buy.')).toBeTruthy();

    // a line without a number goes by its plan; pay as you go sells no add-ons
    const other = within(second as HTMLElement);
    expect(other.getByRole('heading', { name: 'Pay as you go' })).toBeTruthy();
    expect(other.getByText('No active add-ons.')).toBeTruthy();
    expect(other.queryByText(/Renewal date/)).toBeNull();
    expect(other.queryByText('No add-ons available to buy.')).toBeNull();
  });

  it('reloads the overview after an add-on is bought', async () => {
    let bought = false;
    const { calls } = stubFetch({
      'GET /self-care/v1/account': () => json([]),
      'GET /self-care/v1/line': () =>
        json([
          bought
            ? { ...plan, addOns: [...plan.addOns, { id: 'a2', name: 'Extra data', endDate: null }] }
            : plan,
        ]),
      [`GET /self-care/v1/line/${LINE}/usage`]: () => json(usage(LINE, [])),
      [`GET /self-care/v1/offering?kind=addOn&billingAccountId=${ACCOUNT}`]: () =>
        json([
          { id: 'pack1', name: 'Extra data', firstCharge: { amount: { unit: 'USD', value: 5 } } },
        ]),
      'POST /self-care/v1/addOn': () => {
        bought = true;
        return json({}, { status: 201 });
      },
    });
    render(<Home />);
    fireEvent.click(await screen.findByRole('button', { name: 'Buy Extra data · 5 USD' }));
    expect((await screen.findByRole('status')).textContent).toBe('Add-on order submitted.');
    await waitFor(() =>
      expect(screen.getAllByRole('listitem').map((item) => item.textContent)).toEqual([
        'Roaming pack',
        'Extra data',
      ]),
    );
    expect(calls.filter((call) => call === 'GET /self-care/v1/line')).toHaveLength(2);
  });

  it('reloads the overview after the account tools change the account', async () => {
    let enabled = true;
    stubFetch({
      'GET /self-care/v1/account': () => json([account(enabled)]),
      'GET /self-care/v1/line': () => json([]),
      'GET /self-care/v1/paymentMethod': () => json([]),
      'PUT /self-care/v1/autoTopUp': () => {
        enabled = false;
        return json({});
      },
    });
    render(<Home />);
    expect(await screen.findByText('Auto top-up · On')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Disable auto top-up' }));
    expect(await screen.findByText('Auto top-up · Off')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Disable auto top-up' })).toBeNull();
  });

  it("says why a line's usage could not load", async () => {
    stubFetch({
      'GET /self-care/v1/account': () => json([]),
      'GET /self-care/v1/line': () => json([plan]),
      [`GET /self-care/v1/line/${LINE}/usage`]: () => tmfError(503, 'Usage unavailable'),
    });
    render(<Home />);
    expect((await screen.findByRole('alert')).textContent).toBe('Error: Usage unavailable');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});

describe('self-care add-ons', () => {
  it('says why the add-ons could not load', async () => {
    stubFetch({
      [`GET /self-care/v1/offering?kind=addOn&billingAccountId=${ACCOUNT}`]: () =>
        tmfError(503, 'Catalog unavailable'),
    });
    render(<AddOns line={plan} refresh={() => {}} />);
    expect((await screen.findByRole('alert')).textContent).toBe('Error: Catalog unavailable');
    expect(screen.queryByRole('button')).toBeNull();
  });
});
