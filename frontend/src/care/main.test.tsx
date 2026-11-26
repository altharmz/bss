import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import type { Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { json, pending, stubFetch, tmfError, type Handler } from '../test/fetch';

// The self-care entry point (care/main.tsx) renders into #root as soon as it is imported:
// these tests import the real module into a fresh document, as care/index.html loads it,
// and drive what a customer sees. The roots it creates are recorded to unmount them.
const roots = vi.hoisted(() => [] as Root[]);
vi.mock('react-dom/client', async (load) => {
  const actual = await load<typeof import('react-dom/client')>();
  return {
    ...actual,
    createRoot: (...args: Parameters<typeof actual.createRoot>) => {
      const root = actual.createRoot(...args);
      roots.push(root);
      return root;
    },
  };
});

const SESSION = 'GET /self-care/v1/session';
const noContent = () => new Response(null, { status: 204 });
const emptyHome = {
  'GET /self-care/v1/account': () => json([]),
  'GET /self-care/v1/line': () => json([]),
};

async function boot(routes: Record<string, Handler>, withRoot = true) {
  const stub = stubFetch(routes);
  if (withRoot) {
    const root = document.createElement('div');
    root.id = 'root';
    document.body.append(root);
  }
  vi.resetModules();
  await act(async () => {
    await import('./main');
  });
  return stub;
}

beforeEach(() => {
  roots.length = 0;
});

afterEach(() => {
  act(() => roots.splice(0).forEach((root) => root.unmount()));
  document.getElementById('root')?.remove();
});

describe('self-care entry point', () => {
  it('renders nothing and requests nothing without a #root', async () => {
    const { fetchMock } = await boot({}, false);
    expect(roots).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('says "Opening your account" until the session answers', async () => {
    await boot({ [SESSION]: pending });
    expect(screen.getByRole('status').textContent).toBe('Opening your account…');
  });

  it('opens sign-in for a guest, then signup, and back again', async () => {
    await boot({
      [SESSION]: () => tmfError(401, 'Unauthorized'),
      'GET /self-care/v1/signup/plans': () => json([{ id: 'plan1', name: 'Prepaid S' }]),
    });
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign up for a line' }));
    expect(await screen.findByRole('heading', { name: 'Sign up' })).toBeTruthy();
    expect(await screen.findByRole('option', { name: 'Prepaid S' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back to sign in' }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Sign up' })).toBeNull();
  });

  it('opens the account once the emailed code is verified', async () => {
    const { calls } = await boot({
      [SESSION]: () => tmfError(401, 'Unauthorized'),
      'POST /self-care/v1/session': () => json({}),
      'POST /self-care/v1/session/verify': () => json({}),
      ...emptyHome,
    });
    fireEvent.change(await screen.findByLabelText('Email'), {
      target: { value: 'customer@example.com' },
    });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    fireEvent.change(await screen.findByLabelText('Email code'), { target: { value: '012345' } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
    expect(await screen.findByText('Customer self-care')).toBeTruthy();
    expect(await screen.findByText('No prepaid lines yet.')).toBeTruthy();
    // the session is read once: signing in does not ask again
    expect(calls.filter((call) => call === SESSION)).toHaveLength(1);
  });

  it('says self-care is unavailable on a 404 and opens the account on Retry', async () => {
    let attempts = 0;
    const { calls } = await boot({
      [SESSION]: () => (++attempts === 1 ? tmfError(404, 'Not Found') : json({})),
      ...emptyHome,
    });
    expect((await screen.findByRole('alert')).textContent).toBe('Self-care is unavailable.');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Customer self-care')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(calls.filter((call) => call === SESSION)).toHaveLength(2);
  });

  it("shows the server's reason for any other failure and keeps Retry", async () => {
    let attempts = 0;
    await boot({
      [SESSION]: () =>
        ++attempts < 3 ? tmfError(503, 'Self-care is restarting') : tmfError(401, 'Unauthorized'),
    });
    expect((await screen.findByRole('alert')).textContent).toBe('Error: Self-care is restarting');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    // Retry shows the loading line again, not the old error
    expect(screen.getByRole('status').textContent).toBe('Opening your account…');
    expect((await screen.findByRole('alert')).textContent).toBe('Error: Self-care is restarting');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
  });

  it('signs out with the CSRF header and returns to sign-in', async () => {
    const { fetchMock } = await boot({
      [SESSION]: () => json({}),
      'DELETE /self-care/v1/session': noContent,
      ...emptyHome,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    const signOut = fetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE');
    expect(signOut?.[0]).toBe('/self-care/v1/session');
    expect(new Headers(signOut?.[1]?.headers).get('X-Bss-Request')).toBe('1');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('keeps the account open and says why when sign-out fails, then clears it', async () => {
    let attempts = 0;
    await boot({
      [SESSION]: () => json({}),
      'DELETE /self-care/v1/session': () =>
        ++attempts === 1 ? tmfError(503, 'Try again shortly') : noContent(),
      ...emptyHome,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Error: Try again shortly');
    expect(screen.getByText('Customer self-care')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
