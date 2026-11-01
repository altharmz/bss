import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../app/App';
import { expectUi } from '../test/expectUi';
import { CONTEXT, json, NAV_RUNS, stubFetch, tmfError, type Handler } from '../test/fetch';
import { RUN } from '../test/fixtures';
import { nextRunText, PENDING_MAX_MS, POLL_MS } from './BillingRuns';

const RUNS = '/internal/v1/billingRun';
const LIST = `GET ${RUNS}?offset=0&limit=20`;
const id = (n: number) =>
  `b${n.toString(16).padStart(7, '0')}-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;

type Run = typeof RUN | Record<string, unknown>;

// CONTEXT: business date 2026-10-01, now 12:00 MSK - past BILLING_RUN_AT (00:30), so the
// run of 2026-10-01 is due and a catch-up may reach it.
const LATEST = RUN;
const SEP30 = {
  ...RUN,
  id: id(2),
  runDate: '2026-09-30',
  startedAt: '2026-09-29T21:30:00Z',
  finishedAt: '2026-09-29T21:32:10Z',
  charges: { count: 2, amounts: [{ unit: 'USD', value: 600 }] },
  isLatest: false,
};
const SEP29_FAILED = {
  ...RUN,
  id: id(3),
  runDate: '2026-09-29',
  status: 'failed',
  failureKind: 'accounts',
  startedAt: '2026-09-28T21:30:00Z',
  finishedAt: '2026-09-28T21:30:40Z',
  accounts: { total: 3, ok: 2, failed: 1, blocked: 0 },
  charges: { count: 2, amounts: [{ unit: 'USD', value: 600 }] },
  error: '1 of 3 accounts not billed (1 failed)',
  isLatest: false,
};
const FAILED_LATEST = {
  ...RUN,
  status: 'failed',
  failureKind: 'accounts',
  accounts: { total: 3, ok: 1, failed: 1, blocked: 1 },
  error: '2 of 3 accounts not billed (1 failed, 1 blocked)',
};
const RUNNING = {
  ...RUN,
  status: 'running',
  finishedAt: null,
  accounts: { total: 3, ok: 2, failed: 0, blocked: 0 },
  charges: { count: 2, amounts: [{ unit: 'USD', value: 600 }] },
};

const page = (runs: Run[], total = runs.length) => json(runs, { total });

/** Answers in turn; the last one repeats. */
function sequence(...answers: Handler[]): Handler {
  let i = 0;
  return () => (answers[Math.min(i++, answers.length - 1)] as Handler)();
}

function backend(list: Handler, routes: Record<string, Handler> = {}, context = CONTEXT) {
  return stubFetch({
    'GET /tmf-api/accountManagement/v5/financialAccount?limit=1&fields=id': () =>
      json([], { total: 23 }),
    'GET /internal/v1/context': () => json(context),
    'GET /internal/v1/session': () =>
      json({ username: 'i.petrova', role: 'admin', mustChangePassword: false }),
    'GET /tmf-api/accountManagement/v5/billingAccount?limit=1&fields=id': () =>
      json([], { total: 7 }),
    'GET /tmf-api/productCatalogManagement/v5/productOffering?limit=1&fields=id': () =>
      json([], { total: 4 }),
    [NAV_RUNS]: () => page([LATEST], 9),
    [LIST]: list,
    ...routes,
  });
}

async function open() {
  window.history.replaceState(null, '', '/billing-runs');
  render(<App />);
  return screen.findByRole('heading', { name: 'Billing runs' });
}

const latestPanel = () => screen.getByRole('region', { name: 'Latest run' });
const historyRows = () =>
  screen.getAllByRole('row').filter((row) => within(row).queryAllByRole('cell').length === 7);
const rowOf = (date: string) => historyRows().find((row) => row.textContent?.startsWith(date));
const count = (calls: string[], key: string) => calls.filter((c) => c === key).length;

/** Fake setTimeout only: the screen polls with it (and gives up on a request with it).
 * Testing Library's findBy and waitFor cannot be used then - their wrapper drains with a
 * setTimeout(0) that never fires - so these tests wait with `until`. */
function fakeTimers() {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
}
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
/** Retries `get` (a getBy query or an assertion) until it passes, letting the stubbed
 * fetches answer in between (a real setImmediate: response bodies are read on real
 * macrotasks) - without moving the fake clock. */
async function until<T>(get: () => T): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return get();
    } catch (error) {
      if (i === 100) throw error;
      await act(async () => {
        await new Promise((resolve) => setImmediate(resolve));
      });
    }
  }
}
async function openWithFakeTimers() {
  fakeTimers();
  window.history.replaceState(null, '', '/billing-runs');
  render(<App />);
  await until(() => screen.getByRole('heading', { name: 'Billing runs' }));
}

describe('billing runs: latest run and history', () => {
  it('selects the latest run without eagerly loading outcomes and has no primary on success', async () => {
    const { calls } = backend(() => page([LATEST]));
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    expectUi('ui.billingRuns.latest');
    expectUi('ui.billingRuns.outcomes');
    expect(rowOf('2026-10-01')?.getAttribute('aria-selected')).toBe('true');
    expect(rowOf('2026-10-01')?.classList.contains('density-compact')).toBe(true);
    expect(calls.filter((call) => call.startsWith(`GET ${RUNS}/${RUN.id}`))).toEqual([]);
    expect(document.querySelector('.billing-runs .bss-btn.primary')).toBeNull();
    expect(screen.getByText('Next run 2026-10-02 00:30 · in 12h 30m')).toBeTruthy();
  });

  it('calculates the next run using the configured business zone across DST', () => {
    expect(
      nextRunText(
        { ...RUN, runDate: '2026-10-31' },
        {
          ...CONTEXT,
          timeZone: 'America/New_York',
          billingRunAt: '03:30',
          now: '2026-11-01T04:00:00Z',
        },
      ),
    ).toBe('Next run 2026-11-01 03:30 · in 4h 30m');
  });
  it('shows the latest run with its KPIs and the first history page', async () => {
    const { calls } = backend(() => page([LATEST, SEP30, SEP29_FAILED], 9));
    await open();
    expect(screen.getByText('Nightly at 00:30 UTC+3')).toBeTruthy();
    expect(screen.getAllByLabelText('Loading').length).toBeGreaterThan(0); // skeletons first
    await screen.findByRole('region', { name: 'Latest run' });
    expectUi('ui.billingRuns.list');

    const panel = latestPanel();
    expect(within(panel).getByText('2026-10-01')).toBeTruthy();
    expect(within(panel).getByText('Completed')).toBeTruthy();
    expect(within(panel).getByText('B0000001')).toBeTruthy();
    // times in BILLING_TZ, not the browser's zone (the suite also runs in New York)
    expect(within(panel).getByText('00:30:00')).toBeTruthy();
    expect(within(panel).getByText('00:31:05')).toBeTruthy();
    expect(within(panel).getByText('1 min 05 s')).toBeTruthy();
    expect(within(panel).getByText('4')).toBeTruthy();
    expect(within(panel).getByText('2,241.40')).toBeTruthy();
    expect(within(panel).getByText('Charged, USD')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull(); // no failure banner

    expect(historyRows().map((r) => r.textContent?.slice(0, 10))).toEqual([
      '2026-10-01',
      '2026-09-30',
      '2026-09-29',
    ]);
    expect(within(rowOf('2026-10-01') as HTMLElement).getByText('latest')).toBeTruthy();
    const failed = rowOf('2026-09-29') as HTMLElement;
    expect(within(failed).getByText('Failed')).toBeTruthy();
    expect(within(failed).getByTitle('1 of 3 accounts not billed (1 failed)')).toBeTruthy();
    expect(within(failed).getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(within(rowOf('2026-09-30') as HTMLElement).queryByRole('button')).toBeNull();
    expect(screen.getByText('Showing 1–3 of 9')).toBeTruthy();
    expect(screen.getByText('20 per page · Times in UTC+3')).toBeTruthy();
    // nothing running, nothing queued: no polling
    expect(count(calls, LIST)).toBe(1);
  });

  it('pages through all 23 runs with the existing offset/limit query', async () => {
    const runs = Array.from({ length: 23 }, (_, index) => ({
      ...RUN,
      id: id(index + 1),
      runDate: new Date(Date.UTC(2026, 9, 1 - index)).toISOString().slice(0, 10),
      isLatest: index === 0,
    }));
    const secondPage = `GET ${RUNS}?offset=20&limit=20`;
    const { calls } = backend(() => page(runs.slice(0, 20), 23), {
      [secondPage]: () => page(runs.slice(20), 23),
    });
    await open();
    await screen.findByText('Showing 1–20 of 23');
    expect(historyRows()).toHaveLength(20);
    expect((expectUi('ui.billingRuns.previous') as HTMLButtonElement).disabled).toBe(true);
    expect((expectUi('ui.billingRuns.next') as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Showing 21–23 of 23')).toBeTruthy();
    expect(rowOf('2026-09-11')).toBeDefined();
    expect(historyRows()).toHaveLength(3);
    expect(within(latestPanel()).getByText('2026-10-01')).toBeTruthy();
    expect(calls).toContain(secondPage);
    expect((expectUi('ui.billingRuns.previous') as HTMLButtonElement).disabled).toBe(false);
    expect((expectUi('ui.billingRuns.next') as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('Showing 1–20 of 23')).toBeTruthy();
    expect(rowOf('2026-10-01')).toBeDefined();
  });

  it('marks catch-up and retry runs and their attempt', async () => {
    backend(() =>
      page([
        { ...LATEST, trigger: 'retry', attempt: 2 },
        { ...SEP30, trigger: 'catchUp' },
      ]),
    );
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    expect(within(latestPanel()).getByText('retry')).toBeTruthy();
    expect(within(latestPanel()).getByText('attempt 2')).toBeTruthy();
    expect(within(rowOf('2026-09-30') as HTMLElement).getByText('catch-up')).toBeTruthy();
  });

  it('shows the failing call with Retry, and recovers', async () => {
    backend(
      sequence(
        () => tmfError(503, 'Service Unavailable'),
        () => page([LATEST]),
      ),
    );
    await open();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('Failed to load run history')).toBeTruthy();
    expect(
      within(alert).getByText(
        'GET /internal/v1/billingRun?offset=0&limit=20 → 503 Service Unavailable',
      ),
    ).toBeTruthy();
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('region', { name: 'Latest run' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('has an empty state before the first run', async () => {
    backend(() => page([]));
    await open();
    expect(await screen.findByText('No runs yet')).toBeTruthy();
    expect(
      screen.getByText(
        'The worker bills the first date at 00:30 UTC+3. You can also catch up to a date.',
      ),
    ).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Latest run' })).toBeNull();
  });

  it('warns about a gap the worker will not bill on its own (Q4)', async () => {
    backend(() => page([{ ...LATEST, runDate: '2026-09-27' }]));
    await open();
    expect(
      await screen.findByText(
        /No run since 2026-09-27\. The worker bills only the due date and the day before on its own; catch up to bill 2026-09-28 – 2026-10-01 in order\./,
      ),
    ).toBeTruthy();
  });

  it('shows no gap warning when the worker can catch up on its own', async () => {
    backend(() => page([{ ...LATEST, runDate: '2026-09-29' }]));
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    expect(screen.queryByText(/No run since/)).toBeNull();
  });
});

describe('billing runs: a failed run and retry (Q3: any failed run)', () => {
  it('shows the red banner and retries the latest run from it', async () => {
    const { calls, fetchMock } = backend(
      sequence(
        () => page([FAILED_LATEST, SEP30]),
        () => page([FAILED_LATEST, SEP30]),
      ),
      {
        [`POST ${RUNS}/${RUN.id}/retry`]: () =>
          json(
            {
              request: {
                id: 'r1',
                kind: 'retry',
                upTo: null,
                run: { id: RUN.id, runDate: '2026-10-01' },
                requestedAt: '2026-10-01T09:00:00Z',
                requestedBy: 'i.petrova',
                pickedAt: null,
                doneAt: null,
              },
            },
            { status: 202 },
          ),
      },
    );
    await open();
    const banner = await screen.findByRole('alert');
    expect(within(banner).getByText('Latest run for 2026-10-01 failed.')).toBeTruthy();
    expect(
      within(banner).getByText('2 of 3 accounts were not billed; charges are incomplete.'),
    ).toBeTruthy();
    expect(
      within(latestPanel()).getByText('2 of 3 accounts not billed (1 failed, 1 blocked)'),
    ).toBeTruthy();
    const retry = screen.getByRole('button', { name: 'Retry 1 failed' });
    expect(retry).toBe(expectUi('ui.billingRuns.retry'));
    fireEvent.click(retry);

    expect(await screen.findByText('Retry of the run of 2026-10-01 queued')).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe(`${RUNS}/${RUN.id}/retry`);
    expect(post?.[1]?.body).toBeUndefined(); // the endpoint takes no body
    expect(
      await screen.findByText(/Queued: retry of 2026-10-01\. The worker takes requests/),
    ).toBeTruthy();
    // queued: actions wait for it
    expect(
      (screen.getByRole('button', { name: 'Retry 1 failed' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(count(calls, LIST)).toBe(2); // reloaded at once
  });

  it('says why a run failed as a whole', async () => {
    backend(() =>
      page([{ ...FAILED_LATEST, failureKind: 'run', error: 'OperationalError: gone' }]),
    );
    await open();
    const banner = await screen.findByRole('alert');
    expect(
      within(banner).getByText('The run itself failed; no later date is billed until it succeeds.'),
    ).toBeTruthy();
    expect(within(latestPanel()).getByText('OperationalError: gone')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry run' }).classList.contains('primary')).toBe(
      true,
    );
    expect(screen.queryByRole('button', { name: 'Retry 1 failed' })).toBeNull();
  });

  it('retries an older failed run from its row without opening it', async () => {
    const { fetchMock } = backend(() => page([LATEST, SEP30, SEP29_FAILED]), {
      [`POST ${RUNS}/${id(3)}/retry`]: () =>
        json(
          {
            request: {
              id: 'r2',
              kind: 'retry',
              upTo: null,
              run: { id: id(3), runDate: '2026-09-29' },
              requestedAt: '2026-10-01T09:00:00Z',
              requestedBy: null,
              pickedAt: null,
              doneAt: null,
            },
          },
          { status: 202 },
        ),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    fireEvent.click(
      within(rowOf('2026-09-29') as HTMLElement).getByRole('button', { name: 'Retry' }),
    );
    expect(await screen.findByText('Retry of the run of 2026-09-29 queued')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe(`${RUNS}/${id(3)}/retry`);
  });

  it('shows the server reason when a retry is refused', async () => {
    backend(() => page([FAILED_LATEST]), {
      [`POST ${RUNS}/${RUN.id}/retry`]: () =>
        json(
          {
            '@type': 'Error',
            code: '409',
            reason: 'Conflict',
            message: 'a retry of the run of 2026-10-01 is already queued',
            status: '409',
          },
          { status: 409 },
        ),
    });
    await open();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry 1 failed' }));
    expect(
      await screen.findByText('a retry of the run of 2026-10-01 is already queued'),
    ).toBeTruthy();
    expect(screen.queryByText(/Queued:/)).toBeNull();
  });
});

describe('billing runs: catch up to a date', () => {
  const queued = (dates: string[]) =>
    json(
      {
        request: {
          id: 'c1',
          kind: 'catchUp',
          upTo: dates[dates.length - 1],
          run: null,
          requestedAt: '2026-10-01T09:00:00Z',
          requestedBy: 'i.petrova',
          pickedAt: null,
          doneAt: null,
        },
        dates,
      },
      { status: 202 },
    );

  it('queues a catch-up with the date as a business date and names the dates', async () => {
    const { fetchMock } = backend(() => page([{ ...SEP30, isLatest: true }]), {
      [`POST ${RUNS}/catchUp`]: () => queued(['2026-10-01']),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    const input = screen.getByLabelText('Catch up to') as HTMLInputElement;
    expect(input.value).toBe('2026-10-01'); // the due date
    expect(input.max).toBe('2026-10-01');
    const button = screen.getByRole('button', { name: 'Catch up' });
    expect(button).toBe(expectUi('ui.billingRuns.catchUp'));
    fireEvent.click(button);
    expect(await screen.findByText('Catch-up queued: 1 date, 2026-10-01')).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe(`${RUNS}/catchUp`);
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ upTo: '2026-10-01' });
    expect(screen.getByText(/Queued: catch-up to 2026-10-01\./)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Catch up' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('catches up to an earlier date, several dates in order', async () => {
    const { fetchMock } = backend(() => page([{ ...LATEST, runDate: '2026-09-27' }]), {
      [`POST ${RUNS}/catchUp`]: () => queued(['2026-09-28', '2026-09-29', '2026-09-30']),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    fireEvent.change(screen.getByLabelText('Catch up to'), { target: { value: '2026-09-30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Catch up' }));
    expect(
      await screen.findByText('Catch-up queued: 3 dates, 2026-09-28 – 2026-09-30'),
    ).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ upTo: '2026-09-30' });
  });

  it('says so when nothing is due, and queues nothing', async () => {
    backend(() => page([LATEST]), {
      [`POST ${RUNS}/catchUp`]: () => json({ dates: [] }),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    fireEvent.click(screen.getByRole('button', { name: 'Catch up' }));
    expect(
      await screen.findByText('Nothing to catch up: every date up to 2026-10-01 has a run'),
    ).toBeTruthy();
    expect(screen.queryByText(/Queued:/)).toBeNull();
  });

  it('shows the server reason for a 422', async () => {
    backend(() => page([LATEST]), {
      [`POST ${RUNS}/catchUp`]: () =>
        json(
          {
            '@type': 'Error',
            code: '422',
            reason: 'Unprocessable Entity',
            message: 'the run of 2026-10-01 is due at 00:30 (Europe/Moscow), not before',
            status: '422',
          },
          { status: 422 },
        ),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    fireEvent.click(screen.getByRole('button', { name: 'Catch up' }));
    expect(
      await screen.findByText('the run of 2026-10-01 is due at 00:30 (Europe/Moscow), not before'),
    ).toBeTruthy();
  });

  it('offers only yesterday before BILLING_RUN_AT (Q5)', async () => {
    // 00:10 MSK on 2026-10-01: the run of 2026-10-01 is not due yet
    backend(
      () => page([{ ...SEP30, isLatest: true }]),
      {},
      {
        ...CONTEXT,
        now: '2026-09-30T21:10:00Z',
      },
    );
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    const input = screen.getByLabelText('Catch up to') as HTMLInputElement;
    expect(input.value).toBe('2026-09-30');
    expect(input.max).toBe('2026-09-30');
    fireEvent.change(input, { target: { value: '2026-10-01' } });
    expect((screen.getByRole('button', { name: 'Catch up' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});

describe('billing runs: polling', () => {
  it('polls while a run is running, shows its progress, and stops when it ends', async () => {
    const { calls } = backend(
      sequence(
        () => page([RUNNING, SEP30]),
        () => page([{ ...RUNNING, accounts: { total: 3, ok: 3, failed: 0, blocked: 0 } }, SEP30]),
        () => page([LATEST, SEP30]),
      ),
    );
    await openWithFakeTimers();
    await until(latestPanel);
    expect(within(latestPanel()).getByText('Running')).toBeTruthy();
    expect(within(latestPanel()).getByText('running…')).toBeTruthy();
    expect(within(latestPanel()).getByText('2 of 3 accounts')).toBeTruthy();
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('2');
    expect((screen.getByRole('button', { name: 'Catch up' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(count(calls, LIST)).toBe(1);

    await advance(POLL_MS - 1);
    expect(count(calls, LIST)).toBe(1); // not before the interval
    await advance(1);
    expect(count(calls, LIST)).toBe(2);
    expect(screen.queryByLabelText('Loading')).toBeNull(); // a refresh never blanks the data
    await until(() => within(latestPanel()).getByText('3 of 3 accounts'));

    const navBefore = count(calls, NAV_RUNS);
    await advance(POLL_MS);
    expect(count(calls, LIST)).toBe(3);
    await until(() => within(latestPanel()).getByText('Completed'));
    expect(screen.queryByRole('progressbar')).toBeNull();
    await until(() => expect(count(calls, NAV_RUNS)).toBe(navBefore + 1)); // the badge follows
    expect((screen.getByRole('button', { name: 'Catch up' }) as HTMLButtonElement).disabled).toBe(
      false,
    );

    await advance(POLL_MS * 4);
    expect(count(calls, LIST)).toBe(3); // nothing running: polling stopped
  });

  it('polls after a retry until a run started after it has finished', async () => {
    const queuedAt = '2026-10-01T09:00:00Z';
    const { calls } = backend(
      sequence(
        () => page([FAILED_LATEST]), // first load
        () => page([FAILED_LATEST]), // right after the POST: not picked up yet
        () => page([{ ...RUNNING, attempt: 2, trigger: 'retry', startedAt: queuedAt }]),
        () =>
          page([
            {
              ...LATEST,
              attempt: 2,
              trigger: 'retry',
              startedAt: queuedAt,
              finishedAt: '2026-10-01T09:00:03Z',
            },
          ]),
      ),
      {
        [`POST ${RUNS}/${RUN.id}/retry`]: () =>
          json(
            {
              request: {
                id: 'r1',
                kind: 'retry',
                upTo: null,
                run: { id: RUN.id, runDate: '2026-10-01' },
                requestedAt: queuedAt,
                requestedBy: null,
                pickedAt: null,
                doneAt: null,
              },
            },
            { status: 202 },
          ),
      },
    );
    await openWithFakeTimers();
    fireEvent.click(await until(() => screen.getByRole('button', { name: 'Retry 1 failed' })));
    await until(() => screen.getByText(/Queued: retry of 2026-10-01/));
    await until(() => expect(count(calls, LIST)).toBe(2));

    await advance(POLL_MS);
    expect(count(calls, LIST)).toBe(3);
    await until(() => within(latestPanel()).getByText('Running'));
    await advance(POLL_MS);
    expect(count(calls, LIST)).toBe(4);
    await until(() => within(latestPanel()).getByText('Completed'));
    expect(screen.queryByText(/Queued:/)).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull(); // the banner is gone with the failure

    await advance(POLL_MS * 4);
    expect(count(calls, LIST)).toBe(4); // carried out: polling stopped
  });

  it('stops waiting on a request the worker never picks up, and says so', async () => {
    const { calls } = backend(() => page([FAILED_LATEST]), {
      [`POST ${RUNS}/${RUN.id}/retry`]: () =>
        json(
          {
            request: {
              id: 'r1',
              kind: 'retry',
              upTo: null,
              run: { id: RUN.id, runDate: '2026-10-01' },
              requestedAt: '2026-10-01T09:00:00Z',
              requestedBy: null,
              pickedAt: null,
              doneAt: null,
            },
          },
          { status: 202 },
        ),
    });
    await openWithFakeTimers();
    fireEvent.click(await until(() => screen.getByRole('button', { name: 'Retry 1 failed' })));
    await until(() => screen.getByText(/Queued: retry of 2026-10-01/));
    // a poll answers within the interval, so polls go out once per POLL_MS
    for (let t = 0; t < PENDING_MAX_MS; t += POLL_MS) {
      await advance(POLL_MS);
      await until(() => expect(count(calls, LIST)).toBe(2 + (t + POLL_MS) / POLL_MS));
    }
    await until(() =>
      screen.getByText(/The retry of 2026-10-01 has not run yet\. The worker takes requests/),
    );
    expect(screen.queryByText(/Queued:/)).toBeNull();
    const polls = count(calls, LIST);
    expect(polls).toBe(2 + PENDING_MAX_MS / POLL_MS); // first load, after the POST, then polls
    await advance(POLL_MS * 4);
    expect(count(calls, LIST)).toBe(polls); // stopped
  });

  it('keeps the data when a refresh fails, and says so with Retry', async () => {
    const { calls } = backend(
      sequence(
        () => page([RUNNING]),
        () => tmfError(502, 'Bad Gateway'),
        () => page([LATEST]),
      ),
    );
    await openWithFakeTimers();
    await until(latestPanel);
    await advance(POLL_MS);
    await until(() =>
      screen.getByText(
        /Refresh failed: GET \/internal\/v1\/billingRun\?offset=0&limit=20 → 502 Bad Gateway/,
      ),
    );
    expect(within(latestPanel()).getByText('Running')).toBeTruthy(); // the last data stays
    await advance(POLL_MS * 2);
    expect(count(calls, LIST)).toBe(2); // a failed refresh stops the polling
    const note = screen.getByText(/Refresh failed/);
    fireEvent.click(within(note).getByRole('button', { name: 'Retry' }));
    await until(() => within(latestPanel()).getByText('Completed'));
    expect(count(calls, LIST)).toBe(3);
    expect(screen.queryByText(/Refresh failed/)).toBeNull();
  });
});

describe('billing runs: one run and its accounts', () => {
  const A1 = 'aaaa0001-0000-4000-8000-000000000001';
  const A2 = 'aaaa0002-0000-4000-8000-000000000002';
  const ACCOUNTS = `GET ${RUNS}/${RUN.id}/accounts`;
  const FAILED_ACCOUNT = {
    billingAccount: { id: A1, name: 'Olga Kuznetsova main' },
    status: 'failed',
    error: 'MissingPrice: no recurring price in USD on 2026-10-01',
    waitsFor: null,
    attempts: 2,
    finishedAt: '2026-09-30T21:30:40Z',
  };
  const BLOCKED_ACCOUNT = {
    billingAccount: { id: A2, name: 'Ivan Petrov main' },
    status: 'blocked',
    error: null,
    waitsFor: { id: id(3), runDate: '2026-09-29' },
    attempts: 1,
    finishedAt: '2026-09-30T21:30:41Z',
  };

  it('selects an older run with its own lazy queries, waiting default and secondary Retry', async () => {
    const older = { ...SEP29_FAILED, accounts: { total: 3, ok: 2, failed: 0, blocked: 1 } };
    const { calls, fetchMock } = backend(() => page([LATEST, older]), {
      [`GET ${RUNS}/${older.id}`]: () => json(older),
      [`GET ${RUNS}/${older.id}/accounts?status=blocked&limit=100`]: () => page([BLOCKED_ACCOUNT]),
      [`POST ${RUNS}/${older.id}/retry`]: () =>
        json(
          {
            request: {
              id: 'older-retry',
              kind: 'retry',
              upTo: null,
              run: { id: older.id, runDate: older.runDate },
              requestedAt: '2026-10-01T09:00:00Z',
              requestedBy: null,
              pickedAt: null,
              doneAt: null,
            },
          },
          { status: 202 },
        ),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    expect(calls).not.toContain(`GET ${RUNS}/${older.id}`);
    fireEvent.click(rowOf(older.runDate) as HTMLElement);
    const outcomes = await screen.findByRole('region', { name: `Run for ${older.runDate}` });
    expect(await within(outcomes).findByText('Ivan Petrov main')).toBeTruthy();
    expect(calls).toContain(`GET ${RUNS}/${older.id}`);
    expect(calls).toContain(`GET ${RUNS}/${older.id}/accounts?status=blocked&limit=100`);
    expect(
      within(outcomes)
        .getByRole('button', { name: /Waiting/ })
        .getAttribute('aria-pressed'),
    ).toBe('true');
    expect(rowOf(older.runDate)?.getAttribute('aria-selected')).toBe('true');
    expect(rowOf(LATEST.runDate)?.getAttribute('aria-selected')).toBeNull();
    expect(within(outcomes).getByText('00:30:41')).toBeTruthy();
    const retry = within(outcomes).getByRole('button', { name: 'Retry' });
    expect(retry.classList.contains('secondary')).toBe(true);
    fireEvent.click(retry);
    expect(await screen.findByText('Retry of the run of 2026-09-29 queued')).toBeTruthy();
    expect(
      fetchMock.mock.calls
        .filter(([, init]) => init?.method === 'POST')
        .map(([url, init]) => [url, init?.body]),
    ).toEqual([[`${RUNS}/${older.id}/retry`, undefined]]);
  });

  it('opens a run from its row with the failed accounts first, and filters', async () => {
    const { calls } = backend(() => page([FAILED_LATEST, SEP30]), {
      [`GET ${RUNS}/${RUN.id}`]: () => json(FAILED_LATEST),
      [`${ACCOUNTS}?status=failed&limit=100`]: () => page([FAILED_ACCOUNT]),
      [`${ACCOUNTS}?status=blocked&limit=100`]: () => page([BLOCKED_ACCOUNT]),
      [`${ACCOUNTS}?limit=100`]: () => page([FAILED_ACCOUNT, BLOCKED_ACCOUNT], 3),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    fireEvent.click(rowOf('2026-10-01') as HTMLElement);
    const dialog = await screen.findByRole('region', { name: 'Run for 2026-10-01' });
    expect(await within(dialog).findByText('Olga Kuznetsova main')).toBeTruthy();
    expect(
      within(dialog)
        .getByRole('button', { name: /Failed/ })
        .getAttribute('aria-pressed'),
    ).toBe('true');
    expect(rowOf('2026-10-01')?.getAttribute('aria-selected')).toBe('true');
    expect(
      within(dialog).getByText('MissingPrice: no recurring price in USD on 2026-10-01'),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole('link', { name: 'Olga Kuznetsova main' }).getAttribute('href'),
    ).toBe(`/subscribers/${A1}`);
    expect(within(dialog).getByRole('button', { name: `Copy ${A1}` })).toBeTruthy();
    expect(within(dialog).getByText('3 · ok 1 · failed 1 · blocked 1')).toBeTruthy();
    fireEvent.click(within(dialog).getByText('Run details'));
    expect(within(dialog).getByText('2026-10-01 00:30:00')).toBeTruthy(); // started, BILLING_TZ

    fireEvent.click(within(dialog).getByRole('button', { name: /Waiting/ }));
    expect(await within(dialog).findByText('Waits for the run of 2026-09-29')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: /All/ }));
    expect(await within(dialog).findByText('Showing 2 of 3')).toBeTruthy();
    expect(calls).toContain(`GET ${RUNS}/${RUN.id}`);
  });

  it('opens the latest run from its panel and retries from the dialog', async () => {
    const { fetchMock } = backend(() => page([FAILED_LATEST]), {
      [`GET ${RUNS}/${RUN.id}`]: () => json(FAILED_LATEST),
      [`${ACCOUNTS}?status=failed&limit=100`]: () => page([FAILED_ACCOUNT]),
      [`POST ${RUNS}/${RUN.id}/retry`]: () =>
        json(
          {
            request: {
              id: 'r1',
              kind: 'retry',
              upTo: null,
              run: { id: RUN.id, runDate: '2026-10-01' },
              requestedAt: '2026-10-01T09:00:00Z',
              requestedBy: null,
              pickedAt: null,
              doneAt: null,
            },
          },
          { status: 202 },
        ),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    fireEvent.click(within(latestPanel()).getByRole('button', { name: 'Accounts' }));
    const dialog = await screen.findByRole('region', { name: 'Run for 2026-10-01' });
    await within(dialog).findByText('Olga Kuznetsova main');
    fireEvent.click(screen.getByRole('button', { name: 'Retry 1 failed' }));
    expect(await screen.findByText('Retry of the run of 2026-10-01 queued')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Run for 2026-10-01' })).toBeNull();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe(`${RUNS}/${RUN.id}/retry`);
  });

  it('opens a completed run on all its accounts, without Retry', async () => {
    backend(() => page([LATEST]), {
      [`GET ${RUNS}/${RUN.id}`]: () => json(LATEST),
      [`${ACCOUNTS}?limit=100`]: () => page([{ ...BLOCKED_ACCOUNT, status: 'ok', waitsFor: null }]),
    });
    await open();
    await screen.findByRole('region', { name: 'Latest run' });
    fireEvent.click(rowOf('2026-10-01') as HTMLElement);
    const dialog = await screen.findByRole('region', { name: 'Run for 2026-10-01' });
    expect(await within(dialog).findByText('Ivan Petrov main')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: /Retry/ })).toBeNull();
  });
});
