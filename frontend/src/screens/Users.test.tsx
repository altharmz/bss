import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

const PATH = '/internal/v1/users';
const alice = {
  username: 'alice',
  role: 'user',
  disabled: false,
  mustChangePassword: true,
  createdAt: '2026-10-01T00:00:00Z',
  updatedAt: '2026-10-01T00:00:00Z',
  lastLoginAt: null,
};
function start(
  role = 'admin',
  list: Handler = () => json([alice]),
  write: Handler = () => json(alice),
) {
  const backend = stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role, mustChangePassword: false }),
    'GET /internal/v1/context': () => json(CONTEXT),
    [NAV_RUNS]: noRuns,
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 0 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 0 }),
    [`GET ${PATH}`]: list,
    [`POST ${PATH}`]: write,
    [`PATCH ${PATH}/alice`]: write,
  });
  window.history.replaceState(null, '', '/users');
  render(<App />);
  return backend;
}
function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe('Users', () => {
  it('keeps native form actions in the fixed dialog footer and submits once with Enter', async () => {
    const { fetchMock } = start();
    fireEvent.click(await screen.findByRole('button', { name: 'New user' }));
    const dialog = screen.getByRole('dialog');
    const save = within(dialog).getByRole('button', { name: 'Save' });
    expect(save.closest('footer')?.parentElement).toBe(dialog);
    expect(dialog.querySelector('.bss-dialog-body')?.contains(save)).toBe(false);
    fill('Username', 'bob');
    fill('Initial password', 'initial-password');
    fireEvent.keyDown(screen.getByLabelText('Initial password'), { key: 'Enter' });
    await screen.findByRole('button', { name: 'New user' });
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });

  it('denies a non-admin without requesting users or showing the menu link', async () => {
    const { calls } = start('user');
    await screen.findByRole('heading', { name: 'Not allowed' });
    expect(expectUi('ui.users.denied').textContent).toContain(
      'Only administrators can manage users.',
    );
    expect(calls).not.toContain(`GET ${PATH}`);
    expect(
      within(screen.getByRole('navigation', { name: 'User menu', hidden: true })).queryByText(
        'Users',
      ),
    ).toBeNull();
  });
  it('lists users and pins the create body and CSRF header', async () => {
    const { fetchMock } = start();
    expect(await screen.findByText('Never')).toBeTruthy();
    expectUi('ui.users.list');
    expectUi('ui.session.menu');
    fireEvent.click(screen.getByRole('button', { name: 'New user' }));
    expectUi('ui.users.manage');
    fill('Username', 'bob');
    fill('Initial password', 'initial-password');
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'admin' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByRole('button', { name: 'New user' });
    const init = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'bob',
      role: 'admin',
      password: 'initial-password',
    });
    expect(init?.headers).toMatchObject({
      'X-Bss-Request': '1',
      'Content-Type': 'application/json',
    });
  });
  it('patches only changed role and disabled fields', async () => {
    const { fetchMock } = start();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit alice' }));
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'admin' } });
    fireEvent.click(screen.getByLabelText('Disabled'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByRole('button', { name: 'New user' });
    const init = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({ role: 'admin', disabled: true });
    expect(init?.headers).toMatchObject({
      'X-Bss-Request': '1',
      'Content-Type': 'application/json',
    });
  });
  it('pins password reset and displays a rejected change without closing', async () => {
    const { fetchMock } = start('admin', undefined, () => tmfError(409, 'Cannot reset this user'));
    fireEvent.click(await screen.findByRole('button', { name: 'Reset password for alice' }));
    fill('Initial password', 'replacement-password');
    fireEvent.click(screen.getByRole('button', { name: 'Reset password' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Cannot reset this user');
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect((screen.getByLabelText('Initial password') as HTMLInputElement).value).toBe('');
    expect(
      JSON.parse(
        String(fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1]?.body),
      ),
    ).toEqual({ password: 'replacement-password' });
  });
  it('creates an mcp user with the password it keeps and marks it MCP only', async () => {
    const mcpUser = {
      ...alice,
      username: 'claude-desktop',
      role: 'mcp',
      mustChangePassword: false,
    };
    const { fetchMock } = start('admin', () => json([alice, mcpUser]));
    expect(await screen.findByText('mcp (MCP only)')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'New user' }));
    fill('Username', 'bob');
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'mcp' } });
    expect(screen.getByText(/An MCP user keeps this password/)).toBeTruthy();
    fill('Initial password', 'initial-password');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByRole('button', { name: 'New user' });
    const init = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      username: 'bob',
      role: 'mcp',
      password: 'initial-password',
    });
  });
  it('sends a new password with a role change to mcp and none within the UI roles', async () => {
    const { fetchMock } = start();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit alice' }));
    expect(screen.queryByLabelText('New password')).toBeNull();
    fireEvent.change(screen.getByLabelText('Role'), { target: { value: 'mcp' } });
    fill('New password', 'replacement-password');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByRole('button', { name: 'New user' });
    const init = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')?.[1];
    expect(JSON.parse(String(init?.body))).toEqual({
      role: 'mcp',
      password: 'replacement-password',
    });
  });
  it('shows empty and failing lists with Retry', async () => {
    let fail = true;
    start('admin', () => (fail ? tmfError(503, 'Service Unavailable') : json([])));
    await screen.findByText('Failed to load users');
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No users')).toBeTruthy();
  });

  // #499: closing sends nothing, an unchanged edit sends nothing, and a change to one's own
  // user ends the session instead of reloading the list
  it('closes the form with Cancel or Escape, and an unchanged edit, without a request', async () => {
    const { fetchMock, calls } = start();
    fireEvent.click(await screen.findByRole('button', { name: 'New user' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit alice' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Edit alice' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(fetchMock.mock.calls.filter(([, init]) => (init?.method ?? 'GET') !== 'GET')).toEqual(
      [],
    );
    expect(calls.filter((c) => c === `GET ${PATH}`)).toHaveLength(1);
  });

  it("ends the session after a change to one's own user", async () => {
    const self = { ...alice, username: 'i.petrova', role: 'admin' };
    const unauthorized = vi.fn();
    window.addEventListener('bss:unauthorized', unauthorized);
    try {
      const { fetchMock, calls } = stubFetch({
        'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
          json([], { total: 23 }),
        'GET /internal/v1/session': () =>
          json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
        'GET /internal/v1/context': () => json(CONTEXT),
        [NAV_RUNS]: noRuns,
        'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
          json([], { total: 0 }),
        'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
          json([], { total: 0 }),
        [`GET ${PATH}`]: () => json([self]),
        [`PATCH ${PATH}/i.petrova`]: () => json(self),
      });
      window.history.replaceState(null, '', '/users');
      render(<App />);
      fireEvent.click(await screen.findByRole('button', { name: 'Reset password for i.petrova' }));
      fill('Initial password', 'replacement-password');
      fireEvent.click(screen.getByRole('button', { name: 'Reset password' }));
      await waitFor(() => expect(unauthorized).toHaveBeenCalledOnce());
      expect(
        fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH').map(([url]) => url),
      ).toEqual([`${PATH}/i.petrova`]);
      expect(calls.filter((c) => c === `GET ${PATH}`)).toHaveLength(1);
    } finally {
      window.removeEventListener('bss:unauthorized', unauthorized);
    }
  });
});
