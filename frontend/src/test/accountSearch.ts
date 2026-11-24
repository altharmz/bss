import { fireEvent, screen } from '@testing-library/react';
import { json, type Handler } from './fetch';
import { expectUi } from './expectUi';

// The billing-account search of the money screens (#466): the Subscribers list's search
// (GET /internal/v1/subscriber?q=) as a combobox; picking a row sets ?account= and the chip.

/** The search request for `q` and its one-row answer: account `id` named `name`. */
export function accountSearchRoute(q: string, id: string, name: string): Record<string, Handler> {
  return {
    [`GET /internal/v1/subscriber?q=${encodeURIComponent(q)}&offset=0&limit=50`]: () =>
      json([
        {
          id,
          name,
          state: 'Active',
          ratingType: 'postpaid',
          customer: { id: 'p1', name: `${name} (customer)` },
          currency: 'USD',
          balance: { unit: 'USD', value: 0 },
          creditLimit: null,
          cycleDay: 1,
          financialAccount: null,
          activeProducts: 1,
          customerAccounts: 1,
        },
      ]),
  };
}

/** Type `q` into the screen's account search (`<ui>.accountSearch`) and pick `name`'s row;
 * resolves with the account chip once it shows. */
export async function searchAccount(ui: string, q: string, name: string): Promise<HTMLElement> {
  const input = expectUi(`${ui}.accountSearch`).querySelector('input') as HTMLElement;
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: q } });
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(`· ${name} ·`) }));
  await screen.findByText(new RegExp(`Account ${name}`), {
    selector: `[data-ui="${ui}.accountChip"]`,
  });
  return expectUi(`${ui}.accountChip`);
}
