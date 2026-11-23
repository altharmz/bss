import { fireEvent, render, screen } from '@testing-library/react';
import { it, expect } from 'vitest';
import { stubFetch, json, tmfError } from '../test/fetch';
import { Home } from './Home';
import { expectUi } from '../test/expectUi';
it('retries a refused overview and shows the empty account', async () => {
  let attempts = 0;
  stubFetch({
    'GET /self-care/v1/account': () => (++attempts === 1 ? tmfError(503, 'Unavailable') : json([])),
    'GET /self-care/v1/line': () => json([]),
  });
  render(<Home />);
  expect((await screen.findByRole('alert')).textContent).toContain('Unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByText('No prepaid lines yet.');
  expectUi('ui.care.home');
});
