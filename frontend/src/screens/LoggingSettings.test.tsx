import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';
import { minutesLeft } from './LoggingSettings';

// The Logging settings screen (docs/ui.md); the PUT bodies are replayed against the real
// API in tests/internal/test_ui_requests.py.

const PATH = '/internal/v1/logging';
const base = {
  baseLevel: null,
  envLevel: 'INFO',
  effectiveLevel: 'INFO',
  debugUntil: null,
  updatedAt: '2026-10-01T08:00:00Z',
  updatedBy: 'i.petrova',
};
const inMinutes = (n: number) => new Date(Date.now() + n * 60_000 - 5_000).toISOString();

function backend(role: 'admin' | 'user', extra: Record<string, Handler>) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () => json({ username: 'u', role, mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    ...extra,
  });
}

const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);

async function open() {
  window.history.replaceState(null, '', '/settings/logging');
  render(<App />);
  await screen.findByRole('heading', { name: 'Logging' });
}

describe('logging settings', () => {
  it('counts whole minutes up and stops at the end', () => {
    const now = Date.parse('2026-10-01T12:00:00Z');
    expect(minutesLeft(null, now)).toBeNull();
    expect(minutesLeft('2026-10-01T12:00:00Z', now)).toBeNull();
    expect(minutesLeft('2026-10-01T12:00:01Z', now)).toBe(1);
    expect(minutesLeft('2026-10-01T12:23:00Z', now)).toBe(23);
  });

  it('shows the state: default base, debug off, last change, no Stop', async () => {
    backend('admin', { [`GET ${PATH}`]: () => json(base) });
    await open();
    expectUi('ui.logging.settings');
    await screen.findByText('Default (INFO)', { selector: 'dd' });
    expect(screen.getByText('off')).toBeTruthy();
    expect(screen.getByText(/i\.petrova, 2026-10-01/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Stop debug' })).toBeNull();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('sends the exact pinned body of each button and of the base level select', async () => {
    let state: Record<string, unknown> = base;
    const { fetchMock } = backend('admin', {
      [`GET ${PATH}`]: () => json(state),
      [`PUT ${PATH}`]: () => json(state),
    });
    await open();
    await screen.findByText('Default (INFO)', { selector: 'dd' });
    for (const [label, body] of [
      ['Debug 15 min', { debugMinutes: 15 }],
      ['Debug 30 min', { debugMinutes: 30 }],
      ['Debug 60 min', { debugMinutes: 60 }],
    ] as const) {
      fireEvent.click(screen.getByRole('button', { name: label }));
      await waitFor(() => expect(writes(fetchMock).at(-1)?.[2]).toBe(JSON.stringify(body)));
    }
    state = { ...base, effectiveLevel: 'DEBUG', debugUntil: inMinutes(23) };
    fireEvent.click(screen.getByRole('button', { name: 'Debug 15 min' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Stop debug' }));
    await waitFor(() =>
      expect(writes(fetchMock).at(-1)?.[2]).toBe(JSON.stringify({ debugMinutes: 0 })),
    );
    state = { ...base, baseLevel: 'WARNING', effectiveLevel: 'WARNING' }; // what the re-read returns
    fireEvent.change(screen.getByLabelText('Base level'), { target: { value: 'WARNING' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(fetchMock).at(-1)?.[2]).toBe(JSON.stringify({ baseLevel: 'WARNING' })),
    );
    await screen.findByText('WARNING', { selector: 'dd' });
    fireEvent.change(screen.getByLabelText('Base level'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(fetchMock).at(-1)?.[2]).toBe(JSON.stringify({ baseLevel: null })),
    );
    expect(writes(fetchMock).every(([m, u]) => m === 'PUT' && u === PATH)).toBe(true);
  });

  it('shows DEBUG with the time left and Stop while debug is active', async () => {
    backend('admin', {
      [`GET ${PATH}`]: () =>
        json({ ...base, baseLevel: 'WARNING', effectiveLevel: 'DEBUG', debugUntil: inMinutes(23) }),
    });
    await open();
    const text = await screen.findByText(/Debug until \d\d:\d\d:\d\d, 23 min left/);
    expect(text).toBeTruthy();
    expect(screen.getByText('DEBUG')).toBeTruthy();
    expect(screen.getByText('WARNING', { selector: 'dd' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Stop debug' })).toBeTruthy();
  });

  it('shows the standard error state when the API refuses a user', async () => {
    backend('user', {
      [`GET ${PATH}`]: () => tmfError(403, 'administrator role required'),
    });
    await open();
    await screen.findByText(/administrator role required/);
    expect(screen.queryByRole('button', { name: 'Debug 15 min' })).toBeNull();
  });

  it('lists Logging in the navigation for an admin only', async () => {
    backend('admin', { [`GET ${PATH}`]: () => json(base) });
    await open();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).getByRole('link', { name: 'Logging' })).toBeTruthy();
  });

  it('hides Logging from a user', async () => {
    backend('user', { [`GET ${PATH}`]: () => tmfError(403, 'administrator role required') });
    await open();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).queryByRole('link', { name: 'Logging' })).toBeNull();
  });
});
