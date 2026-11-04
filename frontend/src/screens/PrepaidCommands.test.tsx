import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, type Handler } from '../test/fetch';

const COMMANDS = '/internal/v1/prepaid/command';
const COMMAND_ID = 'c0000001-0000-4000-8000-000000000001';
const ACCOUNT_ID = 'a0000001-0000-4000-8000-000000000001';

function command(state = 'failedTechnical') {
  return {
    id: COMMAND_ID,
    kind: 'order',
    state,
    billingAccount: { id: ACCOUNT_ID, name: 'Olga Kuznetsova main' },
    dueDate: '2026-10-01',
    createdAt: '2026-10-01T06:00:00Z',
    attempts: 1,
    attemptedAt: '2026-10-01T06:00:00Z',
    nextRetryAt: state === 'failedTechnical' ? '2026-10-01T06:01:00Z' : null,
    outcome: null,
    reason: null,
    error: state === 'failedTechnical' ? 'connection reset' : null,
    needs_topup: null,
    productOrder: null,
    products: [],
    billingRun: null,
    bills: [],
  };
}

function backend(routes: Record<string, Handler>) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    ...routes,
  });
}

describe('prepaid commands', () => {
  it('retries a failedTechnical command with the pinned request', async () => {
    let state = 'failedTechnical';
    const { calls, fetchMock } = backend({
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([command(state)], { total: 1 }),
      [`GET ${COMMANDS}/${COMMAND_ID}`]: () => json(command(state)),
      [`POST ${COMMANDS}/${COMMAND_ID}/retry`]: () => {
        state = 'pending';
        return json(command(state), { status: 202 });
      },
    });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);

    // The Id button copies and stops the click, so the row opens from another cell.
    expect(await screen.findByText('C0000001')).toBeTruthy();
    expect(screen.getByText('CMD-')).toBeTruthy();
    expect(screen.getByText('BA-')).toBeTruthy();
    expect(
      screen
        .getAllByRole('row')
        .slice(1)
        .every((r) => r.classList.contains('density-dense')),
    ).toBe(true);
    fireEvent.click(screen.getByText('order'));
    const dialog = await screen.findByRole('dialog', { name: 'Command C0000001' });
    expectUi('ui.prepaidCommands.detail');
    expect(within(dialog).getAllByText('Failed (technical)')).not.toHaveLength(0);
    const retry = within(dialog).getByRole('button', { name: 'Retry' });
    expect(retry).toBe(expectUi('ui.prepaidCommands.retry'));
    fireEvent.click(retry);

    await waitFor(() => expect(within(dialog).getAllByText('Queued')).not.toHaveLength(0));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe(`${COMMANDS}/${COMMAND_ID}/retry`);
    expect(post?.[1]?.body).toBeUndefined();
    expect(calls.filter((call) => call === `GET ${COMMANDS}?offset=0&limit=50`)).toHaveLength(2);
  });
});
