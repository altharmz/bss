import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { NetworkCommands } from './NetworkCommands';
import { SystemContextProvider } from '../app/context';
import { CONTEXT, json, stubFetch, tmfError } from '../test/fetch';
import { expectUi } from '../test/expectUi';

// #498: a failed read of a product's network commands says so and retries in place.
const P1 = 'c0000001-0000-4000-8000-000000000001';
const LIST = `GET /internal/v1/network/command?product.id=${P1}&offset=0&limit=100`;
const COMMAND = {
  id: 'b0000001-0000-4000-8000-000000000001',
  kind: 'activate',
  state: 'confirmed',
  productOrder: { id: 'o1' },
  productOrderItem: { id: '1' },
  product: { id: P1 },
  dueDate: '2026-08-01',
  profile: {},
  request: null,
  attempts: 1,
  attemptedAt: '2026-08-01T09:00:00Z',
  nextAttemptAt: null,
  lastError: null,
  refusalCode: null,
  confirmedAt: '2026-08-01T09:00:01Z',
  createdAt: '2026-08-01T08:00:00Z',
};

describe('network commands', () => {
  it('says the commands failed to load and Retry shows them', async () => {
    let reads = 0;
    stubFetch({
      [LIST]: () => (++reads === 1 ? tmfError(503, 'Unavailable') : json([COMMAND])),
    });
    render(
      <SystemContextProvider value={CONTEXT}>
        <NetworkCommands owner={{ productId: P1 }} />
      </SystemContextProvider>,
    );
    const failed = await screen.findByText(/Failed to load the network commands/);
    const note = failed.closest('[data-ui="ui.network.commands"]') as HTMLElement;
    expect(note.textContent).toContain('Unavailable');
    fireEvent.click(within(note).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/Failed to load/)).toBeNull());
    expect(within(expectUi('ui.network.commands')).getByText('Activate')).toBeTruthy();
    expect(reads).toBe(2);
  });
});
