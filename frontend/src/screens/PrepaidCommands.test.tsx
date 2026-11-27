import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { accountSearchRoute, searchAccount } from '../test/accountSearch';
import { expectUi } from '../test/expectUi';
import {
  CONTEXT,
  json,
  NAV_RUNS,
  noRuns,
  pending,
  stubFetch,
  tmfError,
  type Handler,
} from '../test/fetch';

const COMMANDS = '/internal/v1/prepaid/command';
const COMMAND_ID = 'c0000001-0000-4000-8000-000000000001';
const ACCOUNT_ID = 'a0000001-0000-4000-8000-000000000001';

function command(state = 'failedTechnical', extra: Record<string, unknown> = {}) {
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
    ...extra,
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
    expectUi('ui.prepaidCommands');
    expectUi('ui.prepaidCommands.previous');
    expectUi('ui.prepaidCommands.next');
  });

  it('filters by the account in the URL, with a removable chip', async () => {
    const { calls } = backend({
      [`GET ${COMMANDS}?billingAccount.id=${ACCOUNT_ID}&offset=0&limit=50`]: () =>
        json([command('pending')], { total: 1 }),
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([], { total: 0 }),
    });
    window.history.replaceState(null, '', `/prepaid-commands?account=${ACCOUNT_ID}`);
    render(<App />);

    expect(await screen.findByText('C0000001')).toBeTruthy();
    const chip = await waitFor(() => expectUi('ui.prepaidCommands.accountChip'));
    expect(chip.textContent).toContain('Account Olga Kuznetsova main');
    fireEvent.click(within(chip).getByRole('button', { name: 'Remove the account filter' }));

    await waitFor(() => expect(calls).toContain(`GET ${COMMANDS}?offset=0&limit=50`));
    expect(screen.queryByText('Remove the account filter')).toBeNull();
    expect(window.location.search).toBe('');
  });

  it('searches a billing account to filter by, back on the first page, and clears it', async () => {
    // #466: the search sets ?account= and the chip; a filter change resets paging
    const { calls } = backend({
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([command('done')], { more: true }),
      [`GET ${COMMANDS}?offset=50&limit=50`]: () => json([command('pending')]),
      [`GET ${COMMANDS}?billingAccount.id=${ACCOUNT_ID}&offset=0&limit=50`]: () =>
        json([command('pending')]),
      ...accountSearchRoute('BA-A0000001', ACCOUNT_ID, 'Olga Kuznetsova main'),
    });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);
    await screen.findByText('Showing 1–1');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Showing 51–51');
    await searchAccount('ui.prepaidCommands', 'BA-A0000001', 'Olga Kuznetsova main');
    expect(window.location.search).toBe(`?account=${ACCOUNT_ID}`);
    await waitFor(() =>
      expect(calls).toContain(`GET ${COMMANDS}?billingAccount.id=${ACCOUNT_ID}&offset=0&limit=50`),
    );
    expect(await screen.findByText('Showing 1–1')).toBeTruthy();
    const chip = expectUi('ui.prepaidCommands.accountChip');
    fireEvent.click(within(chip).getByRole('button', { name: 'Remove the account filter' }));
    await waitFor(() =>
      expect(calls.filter((c) => c === `GET ${COMMANDS}?offset=0&limit=50`)).toHaveLength(2),
    );
    expect(window.location.search).toBe('');
  });

  it('says a command waits for a top-up and links to the card', async () => {
    const waiting = command('pending', {
      needs_topup: {
        required: 650,
        available: 100,
        shortfall: 550,
        observedAt: '2026-10-01T06:00:00Z',
      },
    });
    backend({
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([waiting], { total: 1 }),
      [`GET ${COMMANDS}/${COMMAND_ID}`]: () => json(waiting),
    });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);

    expect(await screen.findByText('C0000001')).toBeTruthy();
    fireEvent.click(screen.getByText('order'));
    const dialog = await screen.findByRole('dialog', { name: 'Command C0000001' });
    const note = await waitFor(() => expectUi('ui.prepaidCommands.needsTopup'));
    expect(dialog.contains(note)).toBe(true);
    expect(note.textContent).toContain('requires 650.00, available 100.00, short 550.00');
    expect(within(note).getByRole('link', { name: 'Open the card to top up' })).toBeTruthy();
  });

  // #499: the paths the first tests leave out - what the operator sees and what is sent.
  it('shows an outcome with its reason, and an outcome alone, as the row result', async () => {
    const rejected = command('completed', { outcome: 'rejected', reason: 'insufficientFunds' });
    const done = command('completed', {
      id: 'c0000002-0000-4000-8000-000000000002',
      outcome: 'applied',
    });
    backend({ [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([rejected, done], { total: 2 }) });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);

    expect(await screen.findByText('rejected: insufficientFunds')).toBeTruthy();
    expect(screen.getByText('applied')).toBeTruthy();
  });

  it('asks for a state from the first page again, and pages back with Previous', async () => {
    const { calls } = backend({
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([command('pending')], { more: true }),
      [`GET ${COMMANDS}?offset=50&limit=50`]: () => json([command('pending')]),
      [`GET ${COMMANDS}?state=failedTechnical&offset=0&limit=50`]: () =>
        json([command()], { more: true }),
      [`GET ${COMMANDS}?state=failedTechnical&offset=50&limit=50`]: () => json([command()]),
    });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);
    await screen.findByText('Showing 1–1');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Showing 51–51');

    fireEvent.click(screen.getByRole('button', { name: 'Failed technically' }));
    expect(await screen.findByText('Showing 1–1')).toBeTruthy();
    expect(calls).toContain(`GET ${COMMANDS}?state=failedTechnical&offset=0&limit=50`);
    expect(calls).not.toContain(`GET ${COMMANDS}?state=failedTechnical&offset=50&limit=50`);

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await screen.findByText('Showing 51–51');
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('Showing 1–1')).toBeTruthy();
    expect(
      calls.filter((c) => c === `GET ${COMMANDS}?state=failedTechnical&offset=0&limit=50`),
    ).toHaveLength(2);
  });

  it('opens the card from the account link of a row without opening the command', async () => {
    backend({
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([command('pending')], { total: 1 }),
      'GET /*': pending,
    });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);
    fireEvent.click(await screen.findByRole('link', { name: 'Olga Kuznetsova main' }));

    expect(window.location.pathname).toBe(`/billing-accounts/${ACCOUNT_ID}`);
    expect(screen.queryByRole('dialog', { name: 'Command C0000001' })).toBeNull();
  });

  it('shows a refused retry in the dialog and keeps Retry offered; Close sends nothing', async () => {
    const { fetchMock } = backend({
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([command()], { total: 1 }),
      [`GET ${COMMANDS}/${COMMAND_ID}`]: () => json(command()),
      [`POST ${COMMANDS}/${COMMAND_ID}/retry`]: () => tmfError(409, 'command is not retryable'),
    });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);
    fireEvent.click(await screen.findByText('order'));
    const dialog = await screen.findByRole('dialog', { name: 'Command C0000001' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));

    expect(
      await within(dialog).findByText(
        `POST ${COMMANDS}/${COMMAND_ID}/retry → 409 command is not retryable`,
      ),
    ).toBeTruthy();
    const retry = within(dialog).getByRole('button', { name: 'Retry' });
    await waitFor(() => expect(retry).toHaveProperty('disabled', false));
    const posts = () => fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts()).toHaveLength(1);

    // the footer's Close (the header's × has the same name)
    fireEvent.click(within(dialog).getByText('Close', { selector: 'button' }));
    expect(screen.queryByRole('dialog', { name: 'Command C0000001' })).toBeNull();
    expect(posts()).toHaveLength(1);
  });

  it('lists the products and bills a command touched', async () => {
    const renewed = command('completed', {
      kind: 'renewal',
      outcome: 'applied',
      products: [{ id: 'd0000001-0000-4000-8000-000000000001' }],
      bills: [{ id: 'b0000001-0000-4000-8000-000000000001' }],
    });
    backend({
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([renewed], { total: 1 }),
      [`GET ${COMMANDS}/${COMMAND_ID}`]: () => json(renewed),
    });
    window.history.replaceState(null, '', '/prepaid-commands');
    render(<App />);
    fireEvent.click(await screen.findByText('renewal'));
    const dialog = await screen.findByRole('dialog', { name: 'Command C0000001' });

    expect(within(dialog).getByText('PRD-')).toBeTruthy();
    expect(within(dialog).getByText('D0000001')).toBeTruthy();
    expect(within(dialog).getByText('BL-')).toBeTruthy();
    expect(within(dialog).getByText('B0000001')).toBeTruthy();
  });

  it('says no matches for a filtered empty page, and Reset filters shows them all', async () => {
    const { calls } = backend({
      [`GET ${COMMANDS}?billingAccount.id=${ACCOUNT_ID}&offset=0&limit=50`]: () =>
        json([command('pending')], { total: 1 }),
      [`GET ${COMMANDS}?billingAccount.id=${ACCOUNT_ID}&state=failedTechnical&offset=0&limit=50`]:
        () => json([], { total: 0 }),
      [`GET ${COMMANDS}?offset=0&limit=50`]: () => json([command('pending')], { total: 1 }),
    });
    window.history.replaceState(null, '', `/prepaid-commands?account=${ACCOUNT_ID}`);
    render(<App />);
    await screen.findByText('C0000001');
    fireEvent.click(screen.getByRole('button', { name: 'Failed technically' }));

    expect(await screen.findByText('No matches')).toBeTruthy();
    expect(screen.queryByText('No prepaid commands')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));

    await waitFor(() => expect(calls).toContain(`GET ${COMMANDS}?offset=0&limit=50`));
    expect(await screen.findByText('C0000001')).toBeTruthy();
    expect(window.location.search).toBe('');
    expect(screen.getByRole('button', { name: 'All' }).getAttribute('aria-pressed')).toBe('true');
  });
});
