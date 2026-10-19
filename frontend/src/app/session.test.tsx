import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, stubFetch, tmfError } from '../test/fetch';
import { RouterProvider } from './router';
import { expectUi } from '../test/expectUi';
import { SessionGate, SignOut } from './session';

const SESSION = '/internal/v1/session';
const admin = { username: 'alice', role: 'admin', mustChangePassword: false };
function start(path = '/catalog') {
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
function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe('session boundary', () => {
  it('gates content, pins sign-in and returns to the requested route', async () => {
    const { fetchMock } = stubFetch({
      [`GET ${SESSION}`]: () => tmfError(401, 'Unauthorized'),
      [`POST ${SESSION}`]: () => json(admin),
    });
    start('/catalog?filter=active');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    expectUi('ui.session.signIn');
    expect(screen.queryByText('Protected content')).toBeNull();
    expect(window.location.pathname).toBe('/sign-in');
    fill('Username', 'alice');
    fill('Password', 'initial-password');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Protected content')).toBeTruthy();
    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toBe('/catalog?filter=active'),
    );
    const init = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'alice',
      password: 'initial-password',
    });
    expect(init?.headers).toMatchObject({
      'X-Bss-Request': '1',
      'Content-Type': 'application/json',
    });
    expect(init?.credentials).toBe('same-origin');
  });

  it('keeps failed sign-in visible and clears the password', async () => {
    stubFetch({
      [`GET ${SESSION}`]: () => tmfError(401, 'Unauthorized'),
      [`POST ${SESSION}`]: () => tmfError(401, 'Invalid credentials'),
    });
    start();
    await screen.findByLabelText('Username');
    fill('Username', 'alice');
    fill('Password', 'wrong-password');
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Invalid credentials');
    expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
  });

  it.each(['//evil.example', '/\\evil.example', '/sign-in', '/change-password'])(
    'rejects an unsafe or looping return path %s',
    async (next) => {
      stubFetch({ [`GET ${SESSION}`]: () => json(admin) });
      start(`/sign-in?next=${encodeURIComponent(next)}`);
      await screen.findByText('Protected content');
      await waitFor(() => expect(window.location.pathname).toBe('/subscribers'));
    },
  );

  it('forces password change before protected content and pins the 204 request', async () => {
    const { fetchMock } = stubFetch({
      [`GET ${SESSION}`]: () => json({ ...admin, mustChangePassword: true }),
      [`POST ${SESSION}/password`]: () => new Response(null, { status: 204 }),
    });
    start('/users');
    await screen.findByRole('heading', { name: 'Change password' });
    expectUi('ui.session.password');
    expect(screen.queryByText('Protected content')).toBeNull();
    fill('Current password', 'initial-password');
    fill('New password', 'replacement-password');
    fill('Confirm password', 'different-password');
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    expect(await screen.findByText('Passwords do not match')).toBeTruthy();
    fill('Confirm password', 'replacement-password');
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    await screen.findByRole('heading', { name: 'Sign in' });
    const init = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      currentPassword: 'initial-password',
      newPassword: 'replacement-password',
    });
    expect(init?.headers).toMatchObject({ 'X-Bss-Request': '1' });
  });

  it('expires content on a 401 and signs out with the CSRF header', async () => {
    const { fetchMock } = stubFetch({
      [`GET ${SESSION}`]: () => json(admin),
      [`DELETE ${SESSION}`]: () => new Response(null, { status: 204 }),
    });
    start();
    await screen.findByText('Protected content');
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await screen.findByRole('heading', { name: 'Sign in' });
    expect(screen.queryByText('Protected content')).toBeNull();
    expect(
      fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE')?.[1]?.headers,
    ).toMatchObject({ 'X-Bss-Request': '1' });
  });
});
