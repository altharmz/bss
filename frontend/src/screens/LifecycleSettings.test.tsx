import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, noRuns, stubFetch, tmfError, type Handler } from '../test/fetch';

// The Prepaid lifecycle tab of Settings (docs/ui.md). TEST-CHANGE (#414): moved here from the
// Billing settings tab; TEST-CHANGE (#467): a Settings tab at /settings/prepaid-lifecycle. The
// requests and the data-ui markers are unchanged.

function backend(extra: Record<string, Handler> = {}) {
  return stubFetch({
    'GET /internal/v1/context': () => json(CONTEXT),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    [NAV_RUNS]: noRuns,
    ...extra,
  });
}

const writes = (fetchMock: ReturnType<typeof stubFetch>['fetchMock']) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([url, init]) => [init?.method, String(url), init?.body ?? null]);

async function open() {
  window.history.replaceState(null, '', '/settings/prepaid-lifecycle');
  render(<App />);
  await screen.findByRole('heading', { name: /^Settings/ });
}

describe('prepaid lifecycle', () => {
  const lifecyclePath = '/internal/v1/lifecycle/settings';
  const lifecycle = {
    graceDays: 3,
    suspensionDays: 30,
    numberAgingDays: 90,
    paygZeroBalanceDays: 7,
    updatedAt: null,
  };

  it('loads lifecycle durations, validates bounds and saves only changed fields including zero', async () => {
    const { fetchMock } = backend({
      [`GET ${lifecyclePath}`]: () => json(lifecycle),
      [`PUT ${lifecyclePath}`]: () => json({ ...lifecycle, graceDays: 0, suspensionDays: 3650 }),
    });
    await open();
    await screen.findByText('90 days');
    expectUi('ui.billingSettings.lifecycle');
    fireEvent.click(screen.getByRole('button', { name: 'Edit lifecycle settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit lifecycle settings' });
    expectUi('ui.billingSettings.lifecycle.form');
    const save = within(dialog).getByRole('button', { name: 'Save' });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    const grace = within(dialog).getByLabelText('Grace days');
    for (const value of ['', '-1', '1.5', '366']) {
      fireEvent.change(grace, { target: { value } });
      expect((save as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.change(grace, { target: { value: '0' } });
    fireEvent.change(within(dialog).getByLabelText('Suspension days'), {
      target: { value: '3651' },
    });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText('Suspension days'), {
      target: { value: '3650' },
    });
    fireEvent.click(save);
    await screen.findByText('Lifecycle settings saved');
    expect(writes(fetchMock)).toEqual([
      ['PUT', lifecyclePath, JSON.stringify({ graceDays: 0, suspensionDays: 3650 })],
    ]);
  });

  // #499: Cancel closes the edit without a write
  it('cancels the edit without a write', async () => {
    const { fetchMock } = backend({ [`GET ${lifecyclePath}`]: () => json(lifecycle) });
    await open();
    await screen.findByText('90 days');
    fireEvent.click(screen.getByRole('button', { name: 'Edit lifecycle settings' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit lifecycle settings' });
    fireEvent.change(within(dialog).getByLabelText('Grace days'), { target: { value: '5' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
  });

  it('retries a failed lifecycle read', async () => {
    let reads = 0;
    backend({
      [`GET ${lifecyclePath}`]: () =>
        ++reads === 1 ? tmfError(503, 'Unavailable') : json(lifecycle),
    });
    await open();
    await screen.findByText('Failed to load lifecycle settings');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('90 days');
  });

  it.each([422, 503])(
    'keeps lifecycle values after %s and requires refresh for uncertainty',
    async (status) => {
      const { fetchMock } = backend({
        [`GET ${lifecyclePath}`]: () => json(lifecycle),
        [`PUT ${lifecyclePath}`]: () => tmfError(status, 'Policy could not be saved'),
      });
      await open();
      await screen.findByText('90 days');
      fireEvent.click(screen.getByRole('button', { name: 'Edit lifecycle settings' }));
      const dialog = await screen.findByRole('dialog', { name: 'Edit lifecycle settings' });
      fireEvent.change(within(dialog).getByLabelText('Grace days'), { target: { value: '4' } });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
      await within(dialog).findByText(status === 503 ? 'Unsure whether it was saved.' : 'Refused.');
      expect((within(dialog).getByLabelText('Grace days') as HTMLInputElement).value).toBe('4');
      if (status === 503) {
        expect(within(dialog).queryByRole('button', { name: 'Save' })).toBeNull();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh' }));
        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      } else
        expect(within(dialog).getByRole('alert').textContent).toContain(
          'Policy could not be saved',
        );
      expect(writes(fetchMock)).toHaveLength(1);
    },
  );

  it('is the active System > Settings item, with its own breadcrumb', async () => {
    backend({ [`GET ${lifecyclePath}`]: () => json(lifecycle) });
    await open();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(within(nav).queryByRole('link', { name: 'Prepaid lifecycle' })).toBeNull();
    const item = within(nav).getByRole('link', { name: 'Settings' });
    expect(item.getAttribute('aria-current')).toBe('page');
    expect(item.getAttribute('href')).toBe('/settings');
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumbs' });
    expect(await within(crumbs).findByText('Prepaid lifecycle')).toBeTruthy();
    expect(within(crumbs).getByRole('link', { name: 'Settings' }).getAttribute('href')).toBe(
      '/settings',
    );
  });
});
