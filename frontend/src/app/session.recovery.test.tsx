import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, stubFetch, tmfError } from '../test/fetch';
import { RouterProvider } from './router';
import { SessionGate, SignOut } from './session';

const SESSION = '/internal/v1/session';
const admin = { username: 'alice', role: 'admin', mustChangePassword: false };

function start(path: string) {
  window.history.replaceState(null, '', path);
  render(
    <RouterProvider>
      <SessionGate>
        <h1>Protected content</h1>
        <SignOut />
      </SessionGate>
    </RouterProvider>,
  );
}

describe('session recovery', () => {
  it('says why the session could not be checked and checks again on Retry', async () => {
    let attempts = 0;
    const { calls } = stubFetch({
      [`GET ${SESSION}`]: () =>
        ++attempts === 1 ? tmfError(503, 'Session store unavailable') : json(admin),
    });
    start('/catalog');
    expect((await screen.findByRole('alert')).textContent).toBe(
      'GET /internal/v1/session → 503 Session store unavailable',
    );
    // neither the content nor sign-in: the operator is not signed out by an outage
    expect(screen.queryByText('Protected content')).toBeNull();
    expect(window.location.pathname).toBe('/catalog');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Protected content')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(window.location.pathname).toBe('/catalog');
    expect(calls.filter((call) => call === `GET ${SESSION}`)).toHaveLength(2);
  });

  it('cancels a voluntary password change back to the billing accounts', async () => {
    stubFetch({ [`GET ${SESSION}`]: () => json(admin) });
    start('/change-password');
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('Protected content')).toBeTruthy();
    expect(window.location.pathname).toBe('/billing-accounts');
  });

  it('offers no Cancel when the password must change', async () => {
    stubFetch({ [`GET ${SESSION}`]: () => json({ ...admin, mustChangePassword: true }) });
    start('/catalog');
    expect(await screen.findByRole('button', { name: 'Sign out' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
    expect(screen.queryByText('Protected content')).toBeNull();
  });

  it('stays signed in and says why when signing out fails', async () => {
    let attempts = 0;
    stubFetch({
      [`GET ${SESSION}`]: () => json(admin),
      [`DELETE ${SESSION}`]: () =>
        ++attempts === 1 ? tmfError(503, 'Try again shortly') : new Response(null, { status: 204 }),
    });
    start('/catalog');
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));
    expect((await screen.findByRole('alert')).textContent).toBe(
      'DELETE /internal/v1/session → 503 Try again shortly',
    );
    expect(screen.getByText('Protected content')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    await waitFor(() => expect(window.location.pathname).toBe('/sign-in'));
  });
});
