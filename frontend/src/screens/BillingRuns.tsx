import { Note } from '../ui/Note';
import { useEffect, useRef, useState, type MouseEvent, type CSSProperties } from 'react';
import {
  catchUp,
  loadRun,
  loadRunAccounts,
  loadRuns,
  retryRun,
  RUN_HISTORY,
  type BillingRun,
  type Queued,
  type RunOutcome,
} from '../api/bss';
import { explain, type Page } from '../api/client';
import { usePolled } from '../api/usePolled';
import { useResource } from '../api/useResource';
import { useRefreshNav, useSetCrumbs, useSystemContext, type SystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { addDays, businessDate, clockTime, utcLabel } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { formatCents, formatInt, toCents } from '../format/money';
import {
  Button,
  Modal,
  PageTitle,
  Panel,
  StatusPill,
  Segmented,
  TextInput,
  useToast,
} from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// Screen 4 (stage 4, slice g; docs/archive/stage4-design.md section 7). The worker alone bills;
// this screen reads the runs and queues requests for it - a catch-up to a date, or a
// retry of a failed run (any failed run, Q3). Where the design handoff and the stage 4
// note differ, the note wins (Q11): "Run manually for any date" is "Catch up to" a date
// (the dates run in order, never one date on its own), and "Re-run" is "Retry".
//
// While a run is running, or a request this screen queued has not been carried out yet,
// the list is polled every POLL_MS. A queued request counts as carried out once a run
// started at or after the moment it was queued (server times) and none is running; the
// screen stops waiting after PENDING_MAX_MS and says so (locally no worker runs on its
// own: `make worker-once`).

export const POLL_MS = 5_000;
export const PENDING_MAX_MS = 180_000;

interface Pending {
  what: string; // for the note: "catch-up to 2026-10-01", "retry of 2026-10-01"
  queuedAt: number; // the request's requestedAt (server clock), ms
}

/** A queued request has been carried out once a run started at or after it (both server
 * times) and none is running any more. */
function carriedOut(request: Pending, data: Page<BillingRun>): boolean {
  return (
    data.items.some((r) => Date.parse(r.startedAt) >= request.queuedAt) &&
    !data.items.some((r) => r.status === 'running')
  );
}

export function columns(currency: string): Column[] {
  return [
    { key: 'date', label: 'Run date', width: '112px', mono: true },
    { key: 'start', label: 'Start', width: '86px', mono: true },
    { key: 'end', label: 'End', width: '100px', mono: true },
    { key: 'duration', label: 'Duration', width: '104px', mono: true },
    { key: 'status', label: 'Status', width: '112px' },
    { key: 'note', label: 'Note', width: 'minmax(0, 1fr)' },
    { key: 'charges', label: 'Charges', width: '100px', align: 'right', mono: true },
    { key: 'amount', label: `Amount, ${currency}`, width: '140px', align: 'right', mono: true },
  ];
}

/** The latest date whose run is due: today from BILLING_RUN_AT on, else yesterday (Q5) -
 * what a catch-up may reach. */
export function dueDate(context: SystemContext): string {
  const now = clockTime(context.now, context.timeZone).slice(0, 5);
  return now >= context.billingRunAt ? context.businessDate : addDays(context.businessDate, -1);
}

/** "N min SS s" (README), "SS s" under a minute; null while there is no end. */
export function duration(run: BillingRun): string | null {
  if (run.finishedAt === null) return null;
  const seconds = Math.max(
    0,
    Math.round((Date.parse(run.finishedAt) - Date.parse(run.startedAt)) / 1000),
  );
  const ss = String(seconds % 60).padStart(2, '0');
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${ss} s`;
}

/** The run's charges in `currency` (refunds negative), in cents. */
export function amountIn(run: BillingRun, currency: string): number {
  return run.charges.amounts
    .filter((m) => m.unit === currency)
    .reduce((sum, m) => sum + toCents(m.value), 0);
}

const isRunning = (run: BillingRun) => run.status === 'running';

const TRIGGERS: Record<BillingRun['trigger'], string | null> = {
  scheduled: null,
  catchUp: 'catch-up',
  retry: 'retry',
};

function Tags({ run, latest }: { run: BillingRun; latest?: boolean }) {
  const trigger = TRIGGERS[run.trigger];
  return (
    <>
      {latest && <span className="bss-run-tag latest">latest</span>}
      {trigger !== null && <span className="bss-run-tag">{trigger}</span>}
      {run.attempt > 1 && <span className="bss-run-tag">attempt {run.attempt}</span>}
    </>
  );
}

/** Why a failed run failed, in the operator's words. */
function failureText(run: BillingRun): string {
  if (run.failureKind === 'run') {
    return 'The run itself failed; no later date is billed until it succeeds.';
  }
  const missing = run.accounts.failed + run.accounts.blocked;
  return `${formatInt(missing)} of ${formatInt(run.accounts.total)} accounts were not billed; charges are incomplete.`;
}

export function BillingRunsScreen() {
  useSetCrumbs([{ label: 'Billing runs' }], 'billingRuns');
  const context = useSystemContext();
  const toast = useToast();
  const refreshNav = useRefreshNav();
  const due = dueDate(context);
  const [upTo, setUpTo] = useState(due);
  const [queued, setQueued] = useState<Pending | null>(null); // the last request queued here
  const [timedOut, setTimedOut] = useState<Pending | null>(null); // gave up waiting on it
  const [open, setOpen] = useState<BillingRun | null>(null);
  const cols = columns(context.currency);

  const waitingOn = (data: Page<BillingRun>) =>
    queued !== null && !carriedOut(queued, data) && timedOut !== queued ? queued : null;
  const runs = usePolled(
    'runs',
    loadRuns,
    (data) => waitingOn(data) !== null || data.items.some(isRunning),
    POLL_MS,
  );
  const page = runs.status === 'ok' ? runs.data : null;
  const latest = page?.items.find((r) => r.isLatest) ?? null;
  const anyRunning = page?.items.some(isRunning) ?? false;
  const pending = page === null ? queued : waitingOn(page);
  const stale =
    queued !== null && timedOut === queued && page !== null && !carriedOut(queued, page)
      ? queued
      : null;

  useEffect(() => {
    if (queued === null) return;
    const timer = setTimeout(() => setTimedOut(queued), PENDING_MAX_MS);
    return () => clearTimeout(timer);
  }, [queued]);

  // the sidebar's badge follows the latest run
  const key = latest === null ? '' : `${latest.id}:${latest.status}:${latest.attempt}`;
  const seenKey = useRef('');
  useEffect(() => {
    if (key === seenKey.current) return;
    const first = seenKey.current === '';
    seenKey.current = key;
    if (!first) refreshNav();
  }, [key, refreshNav]);

  const [busy, setBusy] = useState(false);
  const perform = async (what: string, call: () => Promise<Queued | null>) => {
    setBusy(true);
    try {
      const result = await call();
      if (result !== null) {
        setQueued({ what, queuedAt: Date.parse(result.requestedAt) });
        runs.refresh();
      }
    } catch (error) {
      toast(explain(error));
    } finally {
      setBusy(false);
    }
  };
  const retry = (run: BillingRun) =>
    void perform(`retry of ${run.runDate}`, async () => {
      const result = await retryRun(run.id);
      toast(`Retry of the run of ${run.runDate} queued`);
      return result;
    });
  const onCatchUp = () =>
    void perform(`catch-up to ${upTo}`, async () => {
      const result = await catchUp(upTo);
      if (result === null) {
        toast(`Nothing to catch up: every date up to ${upTo} has a run`);
      } else {
        const { dates } = result;
        const span = dates.length === 1 ? dates[0] : `${dates[0]} – ${dates[dates.length - 1]}`;
        toast(
          `Catch-up queued: ${formatInt(dates.length)} date${dates.length === 1 ? '' : 's'}, ${span}`,
        );
      }
      return result;
    });
  const waiting = pending !== null || anyRunning;
  const disabled = waiting || busy;

  const validUpTo = /^\d{4}-\d{2}-\d{2}$/.test(upTo) && upTo <= due;
  const gap =
    latest !== null && latest.runDate < addDays(due, -2)
      ? { from: addDays(latest.runDate, 1), to: due }
      : null;

  return (
    <div className="bss-screen" data-ui="ui.billingRuns.list">
      <div className="bss-title-row">
        <PageTitle title="Billing runs" />
        <span className="bss-caption">
          Scheduled daily at {context.billingRunAt} {utcLabel(context.utcOffset)}
        </span>
        <div className="bss-title-actions">
          <label className="bss-caption" htmlFor="catch-up-to">
            Catch up to
          </label>
          <TextInput
            id="catch-up-to"
            type="date"
            mono
            value={upTo}
            max={due}
            onChange={(event) => setUpTo(event.target.value)}
          />
          <Button
            variant="primary"
            data-ui="ui.billingRuns.catchUp"
            disabled={disabled || !validUpTo || runs.status !== 'ok'}
            onClick={onCatchUp}
          >
            Catch up
          </Button>
        </div>
      </div>

      {pending !== null && (
        <div className="bss-run-note" role="status">
          Queued: {pending.what}. The worker takes requests within a minute; this page updates on
          its own.
        </div>
      )}
      {stale !== null && (
        <div className="bss-run-note">
          The {stale.what} has not run yet. The worker takes requests within a minute - is it
          running?{' '}
          <Button small onClick={() => runs.retry()}>
            Reload
          </Button>
        </div>
      )}
      {gap !== null && (
        <div className="bss-run-note warn">
          No run since {latest?.runDate}. The worker bills only the due date and the day before on
          its own; catch up to bill {gap.from} – {gap.to} in order.
        </div>
      )}

      {runs.status === 'loading' && <LatestSkeleton />}
      {latest !== null && latest.status === 'failed' && (
        <div className="bss-run-banner" role="alert">
          <span className="icon" aria-hidden="true">
            !
          </span>
          <span className="strong">Latest run for {latest.runDate} failed.</span>
          <span className="text">{failureText(latest)}</span>
          <button
            type="button"
            className="bss-run-banner-btn"
            data-ui="ui.billingRuns.retry"
            disabled={disabled}
            onClick={() => retry(latest)}
          >
            Retry run for {latest.runDate}
          </button>
        </div>
      )}
      {latest !== null && (
        <LatestRun run={latest} context={context} onOpen={() => setOpen(latest)} />
      )}

      <Panel
        title="Run history"
        subtitle={`last ${RUN_HISTORY} runs`}
        {...(runs.refreshError !== null
          ? {
              right: (
                <Note tone="negative">
                  Refresh failed: {runs.refreshError.message}{' '}
                  <Button small onClick={runs.refresh}>
                    Retry
                  </Button>
                </Note>
              ),
            }
          : {})}
      >
        <TableHead columns={cols} />
        <ListState
          resource={runs}
          columns={cols}
          errorTitle="Failed to load run history"
          empty={{
            title: 'No runs yet',
            text: `The worker bills the first date at ${context.billingRunAt} ${utcLabel(context.utcOffset)}. You can also catch up to a date.`,
          }}
          total={(data: Page<BillingRun>) => data.items.length}
          rows={(data: Page<BillingRun>) => data.items}
          onResetFilters={() => {}}
        >
          {(rows, data) => (
            <>
              {rows.map((run) => (
                <HistoryRow
                  key={run.id}
                  run={run}
                  context={context}
                  busy={disabled}
                  onOpen={() => setOpen(run)}
                  onRetry={() => retry(run)}
                />
              ))}
              <TableFooter
                left={`Showing ${formatInt(rows.length)} of ${formatInt(data.total)}`}
                right={`Times in ${utcLabel(context.utcOffset)}`}
              />
            </>
          )}
        </ListState>
      </Panel>

      {open !== null && (
        <RunModal
          run={open}
          busy={disabled}
          onRetry={(run) => {
            setOpen(null);
            retry(run);
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

function LatestSkeleton() {
  return (
    <div className="bss-run-latest skeleton" aria-busy="true" aria-label="Loading">
      <div className="bss-run-kpis">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="bss-run-kpi">
            <span className="run-skeleton-label bss-skel" />
            <span className="run-skeleton-value bss-skel" />
          </div>
        ))}
      </div>
    </div>
  );
}

function LatestRun({
  run,
  context,
  onOpen,
}: {
  run: BillingRun;
  context: SystemContext;
  onOpen: () => void;
}) {
  const failed = run.status === 'failed';
  const done = run.accounts.ok + run.accounts.failed + run.accounts.blocked;
  const pct = run.accounts.total === 0 ? 0 : Math.round((done / run.accounts.total) * 100);
  return (
    <section className={`bss-run-latest${failed ? ' failed' : ''}`} aria-label="Latest run">
      <header>
        <span className="label">Latest run</span>
        <span className="date">{run.runDate}</span>
        <StatusPill kind="run" status={run.status} />
        <Tags run={run} />
        <span className="id" title={run.id}>
          {shortId(run.id)}
        </span>
        <Button small onClick={onOpen}>
          Accounts
        </Button>
      </header>
      <div className="bss-run-kpis">
        <Kpi label="Start" value={clockTime(run.startedAt, context.timeZone)} />
        <Kpi
          label="End"
          value={run.finishedAt === null ? '—' : clockTime(run.finishedAt, context.timeZone)}
        />
        <Kpi label="Duration" value={duration(run) ?? 'running…'} />
        <Kpi label="Charges" value={formatInt(run.charges.count)} />
        <Kpi
          label={`Amount, ${context.currency}`}
          value={formatCents(amountIn(run, context.currency))}
          strong
        />
      </div>
      {run.status === 'running' && (
        <div className="bss-run-progress">
          <div
            className="track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={run.accounts.total}
            aria-valuenow={done}
          >
            <div
              className="fill run-progress"
              style={{ '--progress': `${pct}%` } as CSSProperties}
            />
          </div>
          <span>
            {formatInt(done)} of {formatInt(run.accounts.total)} accounts
          </span>
        </div>
      )}
      {failed && run.error !== null && <div className="bss-run-error">{run.error}</div>}
    </section>
  );
}

function Kpi({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="bss-run-kpi">
      <span className="label">{label}</span>
      <span className={`value${strong ? ' strong' : ''}`}>{value}</span>
    </div>
  );
}

function HistoryRow({
  run,
  context,
  busy,
  onOpen,
  onRetry,
}: {
  run: BillingRun;
  context: SystemContext;
  busy: boolean;
  onOpen: () => void;
  onRetry: () => void;
}) {
  const failed = run.status === 'failed';
  const retry = (event: MouseEvent) => {
    event.stopPropagation(); // not the row's click (the detail)
    onRetry();
  };
  return (
    <TableRow
      columns={columns(context.currency)}
      onClick={onOpen}
      className={`run-history-row${run.isLatest ? ' is-latest' : ''}${failed ? ' is-failed' : ''}`}
      cells={{
        date: run.runDate,
        start: <span className="bss-text-2">{clockTime(run.startedAt, context.timeZone)}</span>,
        end: (
          <span className="bss-text-2">
            {run.finishedAt === null ? '—' : clockTime(run.finishedAt, context.timeZone)}
          </span>
        ),
        duration: <span className="bss-text-2">{duration(run) ?? 'running…'}</span>,
        status: <StatusPill kind="run" status={run.status} />,
        note: (
          <span className="bss-run-note-cell">
            <Tags run={run} latest={run.isLatest} />
            {failed && run.error !== null && (
              <span className="error ellipsis" title={run.error}>
                {run.error}
              </span>
            )}
            {failed && (
              <button
                type="button"
                className="bss-link"
                data-ui="ui.billingRuns.retry"
                disabled={busy}
                onClick={retry}
              >
                Retry
              </button>
            )}
          </span>
        ),
        charges: formatInt(run.charges.count),
        amount: (
          <span className={failed ? 'bss-faint' : undefined}>
            {formatCents(amountIn(run, context.currency))}
          </span>
        ),
      }}
    />
  );
}

// --- one run: its summary and per-account outcomes ---

type OutcomeFilter = RunOutcome | 'all';
const FILTER_LABELS: Record<OutcomeFilter, string> = {
  failed: 'Failed',
  blocked: 'Blocked',
  ok: 'OK',
  all: 'All',
};

const ACCOUNT_COLUMNS: Column[] = [
  { key: 'account', label: 'Billing account', width: '1fr' },
  { key: 'status', label: 'Status', width: '100px' },
  { key: 'note', label: 'Note', width: '1.2fr' },
  { key: 'attempts', label: 'Attempts', width: '90px', align: 'right', mono: true },
];

function firstFilter(run: BillingRun): OutcomeFilter {
  if (run.accounts.failed > 0) return 'failed';
  if (run.accounts.blocked > 0) return 'blocked';
  return 'all';
}

function RunModal({
  run: initial,
  busy,
  onRetry,
  onClose,
}: {
  run: BillingRun;
  busy: boolean;
  onRetry: (run: BillingRun) => void;
  onClose: () => void;
}) {
  const context = useSystemContext();
  const fresh = useResource(`run:${initial.id}`, (signal) => loadRun(initial.id, signal));
  const run = fresh.status === 'ok' ? fresh.data : initial;
  const [filter, setFilter] = useState<OutcomeFilter>(firstFilter(initial));
  const accounts = useResource(`run:${initial.id}:accounts:${filter}`, (signal) =>
    loadRunAccounts(initial.id, filter === 'all' ? null : filter, signal),
  );
  const counts: Record<OutcomeFilter, number> = {
    failed: run.accounts.failed,
    blocked: run.accounts.blocked,
    ok: run.accounts.ok,
    all: run.accounts.ok + run.accounts.failed + run.accounts.blocked,
  };
  const when = (instant: string | null) =>
    instant === null
      ? '—'
      : `${businessDate(instant, context.timeZone)} ${clockTime(instant, context.timeZone)}`;

  return (
    <Modal
      title={`Run for ${run.runDate}`}
      subtitle={
        <span className="bss-run-subtitle">
          <StatusPill kind="run" status={run.status} />
          <Tags run={run} latest={run.isLatest} />
          <span className="bss-mono" title={run.id}>
            {shortId(run.id)}
          </span>
        </span>
      }
      width={760}
      onClose={onClose}
      footer={
        <>
          {run.status === 'failed' && (
            <Button
              variant="primary"
              data-ui="ui.billingRuns.retry"
              disabled={busy}
              onClick={() => onRetry(run)}
            >
              Retry run for {run.runDate}
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <dl className="bss-details">
          <dt>Started</dt>
          <dd className="bss-mono">{when(run.startedAt)}</dd>
          <dt>Finished</dt>
          <dd className="bss-mono">{when(run.finishedAt)}</dd>
          <dt>Last progress</dt>
          <dd className="bss-mono">{when(run.heartbeatAt)}</dd>
          <dt>Accounts</dt>
          <dd className="bss-mono">
            {formatInt(run.accounts.total)} · ok {formatInt(run.accounts.ok)} · failed{' '}
            {formatInt(run.accounts.failed)} · blocked {formatInt(run.accounts.blocked)}
          </dd>
          <dt>Charges</dt>
          <dd className="bss-mono">
            {formatInt(run.charges.count)} ·{' '}
            {run.charges.amounts.length === 0
              ? `0.00 ${context.currency}`
              : run.charges.amounts
                  .map((m) => `${formatCents(toCents(m.value))} ${m.unit}`)
                  .join(' · ')}
          </dd>
          {run.error !== null && (
            <>
              <dt>Error</dt>
              <dd className="bss-run-error inline">{run.error}</dd>
            </>
          )}
        </dl>
        <div className="bss-toolbar">
          <Segmented
            label="Account outcome"
            value={filter}
            onChange={setFilter}
            options={(['failed', 'blocked', 'ok', 'all'] as const).map((key) => ({
              key,
              label: FILTER_LABELS[key],
              count: counts[key],
            }))}
          />
        </div>
        <Panel>
          <TableHead columns={ACCOUNT_COLUMNS} />
          <ListState
            resource={accounts}
            columns={ACCOUNT_COLUMNS}
            errorTitle="Failed to load the run's accounts"
            empty={{
              title: filter === 'all' ? 'No accounts yet' : `No ${filter} accounts`,
              text:
                filter === 'all'
                  ? 'Accounts appear as the run bills them.'
                  : 'Nothing in this run has this outcome.',
            }}
            total={(data) => data.items.length}
            rows={(data) => data.items}
            onResetFilters={() => setFilter('all')}
          >
            {(rows, data) => (
              <>
                {rows.map((outcome) => (
                  <TableRow
                    key={outcome.billingAccount.id}
                    columns={ACCOUNT_COLUMNS}
                    cells={{
                      account: (
                        <span className="ellipsis">
                          <Link
                            className="bss-link bss-mono"
                            href={hrefOf({
                              name: 'subscriber',
                              accountId: outcome.billingAccount.id,
                            })}
                          >
                            {accountLabel(outcome.billingAccount.id)}
                          </Link>{' '}
                          {outcome.billingAccount.name}
                        </span>
                      ),
                      status: <StatusPill kind="runOutcome" status={outcome.status} />,
                      note:
                        outcome.status === 'failed' ? (
                          <span className="bss-run-error-text ellipsis" title={outcome.error ?? ''}>
                            {outcome.error}
                          </span>
                        ) : outcome.waitsFor !== null ? (
                          <span className="bss-secondary">
                            waits for the run of {outcome.waitsFor.runDate}
                          </span>
                        ) : (
                          ''
                        ),
                      attempts: formatInt(outcome.attempts),
                    }}
                  />
                ))}
                <TableFooter
                  left={`Showing ${formatInt(rows.length)} of ${formatInt(data.total)}`}
                />
              </>
            )}
          </ListState>
        </Panel>
      </div>
    </Modal>
  );
}
