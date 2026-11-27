// Ledger v2: BillingRuns.dc.html; decisions/frontend.md (Ledger v2, R8).
import { Note } from '../ui/Note';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type CSSProperties,
  type ReactNode,
} from 'react';
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
import { agoLabel, plural } from '../format/labels';
import { useRefreshNav, useSetCrumbs, useSystemContext, type SystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { addDays, businessDate, clockTime, utcLabel, zonedDateTime } from '../format/dates';
import { shortId } from '../format/ids';
import { formatCents, formatInt, toCents } from '../format/money';
import { Button, PageHeader, Panel, StatusPill, Segmented, DateField, useToast } from '../ui/kit';
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
    { key: 'date', label: 'Date', width: '100px', mono: true },
    { key: 'status', label: 'Status', width: '100px' },
    { key: 'trigger', label: 'Trigger', width: 'minmax(60px, 1fr)' },
    { key: 'duration', label: 'Took', width: '90px', mono: true },
    { key: 'failed', label: 'Failed', width: '52px', align: 'right', mono: true },
    { key: 'amount', label: `Charged, ${currency}`, width: '112px', align: 'right' },
    { key: 'actions', label: '', width: '52px', kind: 'actions' },
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
  return `${formatInt(missing)} of ${plural(run.accounts.total, 'account')} ${missing === 1 ? 'was' : 'were'} not billed; charges are incomplete.`;
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
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<BillingRun | null>(null);
  const [latest, setLatest] = useState<BillingRun | null>(null);
  const cols = columns(context.currency);

  const waitingOn = (data: Page<BillingRun>) =>
    queued !== null && !carriedOut(queued, data) && timedOut !== queued ? queued : null;
  const runs = usePolled(
    `runs:${offset}`,
    (signal) =>
      loadRuns(signal, RUN_HISTORY, offset).then((data) => {
        const current = data.items.find((run) => run.isLatest);
        if (current) setLatest(current);
        if (open !== null && !data.items.some((run) => run.id === open.id)) setOpen(null);
        return data;
      }),
    (data) => waitingOn(data) !== null || data.items.some(isRunning),
    POLL_MS,
  );
  const page = runs.status === 'ok' ? runs.data : null;
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
    if (offset !== 0 || key === seenKey.current) return;
    const first = seenKey.current === '';
    seenKey.current = key;
    if (!first) refreshNav();
  }, [key, offset, refreshNav]);

  const [busy, setBusy] = useState(false);
  const perform = async (what: string, call: () => Promise<Queued | null>) => {
    setBusy(true);
    try {
      const result = await call();
      if (result !== null) {
        setQueued({ what, queuedAt: Date.parse(result.requestedAt) });
        if (offset === 0) {
          runs.refresh();
        } else {
          setOffset(0);
        }
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
    <div className="bss-screen billing-runs" data-ui="ui.billingRuns.list">
      <PageHeader
        title="Billing runs"
        meta={
          <>
            Nightly at {context.billingRunAt} {utcLabel(context.utcOffset)}
            {latest !== null &&
              ` · Last run ${latest.runDate} · ${agoLabel(latest.startedAt, context.now)}`}
          </>
        }
        actions={
          <>
            <label className="bss-caption" htmlFor="catch-up-to">
              Catch up to
            </label>
            <DateField
              id="catch-up-to"
              value={upTo}
              max={due}
              onChange={(event) => setUpTo(event.target.value)}
            />
            <Button
              data-ui="ui.billingRuns.catchUp"
              disabled={disabled || !validUpTo || runs.status !== 'ok'}
              onClick={onCatchUp}
            >
              Catch up
            </Button>
            {latest?.status === 'failed' && (
              <Button
                variant="primary"
                data-ui="ui.billingRuns.retry"
                disabled={disabled}
                onClick={() => {
                  setOpen(null);
                  retry(latest);
                }}
              >
                {latest.failureKind === 'run'
                  ? 'Retry run'
                  : `Retry ${formatInt(latest.accounts.failed)} failed`}
              </Button>
            )}
          </>
        }
      />

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

      {latest === null && runs.status === 'loading' && <LatestSkeleton />}
      {latest !== null && (
        <LatestRun run={latest} context={context} onOpen={() => setOpen(latest)} />
      )}

      <div className="run-tables">
        <section className="run-outcomes" aria-label="Outcomes" data-ui="ui.billingRuns.outcomes">
          {open === null ? (
            <>
              <header className="run-section-header">
                <h2>Outcomes</h2>
                {latest !== null && <span className="bss-secondary">run {latest.runDate}</span>}
              </header>
              <Note tone="info">
                {latest === null ? (
                  'Select a run to view its outcomes.'
                ) : (
                  <>
                    Use Accounts above or select a history row to view the selected run’s accounts.
                  </>
                )}
              </Note>
            </>
          ) : (
            <RunOutcomes
              key={open.id}
              run={open}
              busy={disabled}
              onRetry={(run) => {
                setOpen(null);
                retry(run);
              }}
            />
          )}
        </section>
        <section className="run-history" aria-label="Run history">
          <Panel
            title="History"
            subtitle={`${RUN_HISTORY} per page · Times in ${utcLabel(context.utcOffset)}`}
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
            <TableHead columns={cols} density="compact" />
            <ListState
              resource={runs}
              columns={cols}
              errorTitle="Failed to load run history"
              empty={{
                title: 'No runs yet',
                text: `The worker bills the first date at ${context.billingRunAt} ${utcLabel(context.utcOffset)}. You can also catch up to a date.`,
              }}
              count={(data: Page<BillingRun>) => data.items.length}
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
                      selected={run.id === (open ?? latest)?.id}
                      onOpen={() => setOpen(run)}
                      onRetry={() => retry(run)}
                    />
                  ))}
                  <TableFooter
                    left={`Showing ${formatInt(offset + 1)}–${formatInt(offset + rows.length)}`}
                    right={
                      <span className="bss-toolbar">
                        <Button
                          small
                          data-ui="ui.billingRuns.previous"
                          disabled={offset === 0}
                          onClick={() => setOffset(Math.max(0, offset - RUN_HISTORY))}
                        >
                          Previous
                        </Button>
                        <Button
                          small
                          data-ui="ui.billingRuns.next"
                          disabled={!data.more}
                          onClick={() => setOffset(offset + RUN_HISTORY)}
                        >
                          Next
                        </Button>
                      </span>
                    }
                  />
                </>
              )}
            </ListState>
          </Panel>
        </section>
      </div>
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
    <section
      className={`bss-run-latest${failed ? ' failed' : ''}`}
      aria-label="Latest run"
      data-ui="ui.billingRuns.latest"
    >
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
        <Kpi label="Accounts" value={formatInt(run.accounts.total)} />
        <Kpi label="Billed" value={formatInt(run.accounts.ok)} />
        <Kpi label="Failed" value={formatInt(run.accounts.failed)} />
        <Kpi label="Waiting" value={formatInt(run.accounts.blocked)} />
        <Kpi
          label="Took"
          value={duration(run) ?? 'running…'}
          caption={
            <>
              <span>{clockTime(run.startedAt, context.timeZone)}</span> →{' '}
              <span>
                {run.finishedAt === null ? '—' : clockTime(run.finishedAt, context.timeZone)}
              </span>
            </>
          }
        />
        <Kpi
          label={`Charged, ${context.currency}`}
          value={<Amount cents={amountIn(run, context.currency)} />}
          caption={
            <>
              <span>{formatInt(run.charges.count)}</span> charges
            </>
          }
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
      {failed && (
        <Note tone="negative" lead={`Latest run for ${run.runDate} failed.`}>
          <span>{failureText(run)}</span>
          <span>
            {formatInt(run.accounts.failed)} failed · {formatInt(run.accounts.blocked)} waiting
          </span>
          {run.error !== null && <span className="bss-run-error-text">{run.error}</span>}
        </Note>
      )}
      <div className="run-next">{nextRunText(run, context)}</div>
    </section>
  );
}

export function nextRunText(run: Pick<BillingRun, 'runDate'>, context: SystemContext): string {
  const date = addDays(run.runDate, 1);
  const minutes = Math.max(
    0,
    Math.floor(
      (Date.parse(zonedDateTime(date, context.billingRunAt, context.timeZone)) -
        Date.parse(context.now)) /
        60_000,
    ),
  );
  return `Next run ${date} ${context.billingRunAt} · in ${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function Kpi({
  label,
  value,
  caption,
  strong = false,
}: {
  label: string;
  value: ReactNode;
  caption?: ReactNode;
  strong?: boolean;
}) {
  return (
    <div className="bss-run-kpi">
      <span className="label">{label}</span>
      <span className={`value${strong ? ' strong' : ''}`}>{value}</span>
      {caption !== undefined && <span className="run-kpi-caption">{caption}</span>}
    </div>
  );
}

function HistoryRow({
  run,
  context,
  busy,
  selected,
  onOpen,
  onRetry,
}: {
  run: BillingRun;
  context: SystemContext;
  busy: boolean;
  selected: boolean;
  onOpen: () => void;
  onRetry: () => void;
}) {
  const failed = run.status === 'failed';
  return (
    <TableRow
      columns={columns(context.currency)}
      onClick={onOpen}
      density="compact"
      selected={selected}
      className={`run-history-row${run.isLatest ? ' is-latest' : ''}${failed ? ' is-failed' : ''}`}
      cells={{
        date: run.runDate,
        duration: <span className="bss-text-2">{duration(run) ?? 'running…'}</span>,
        status: <StatusPill kind="run" status={run.status} />,
        trigger: (
          <span className="bss-run-note-cell" title={run.error ?? undefined}>
            {run.trigger === 'scheduled' && <span className="bss-run-tag">scheduled</span>}
            <Tags run={run} latest={run.isLatest} />
          </span>
        ),
        failed: (
          <span className={failed ? 'bss-run-error-text' : 'bss-secondary'}>
            {formatInt(run.accounts.failed)}
          </span>
        ),
        amount: <Amount cents={amountIn(run, context.currency)} />,
        actions: failed && (
          <Button
            small
            variant="quiet"
            data-ui="ui.billingRuns.retry"
            disabled={busy}
            onClick={(event: MouseEvent) => {
              event.stopPropagation();
              onRetry();
            }}
          >
            Retry
          </Button>
        ),
      }}
    />
  );
}

// --- one run: its summary and per-account outcomes ---

type OutcomeFilter = RunOutcome | 'all';
const FILTER_LABELS: Record<OutcomeFilter, string> = {
  failed: 'Failed',
  blocked: 'Waiting',
  ok: 'Billed',
  all: 'All',
};

const ACCOUNT_COLUMNS: Column[] = [
  { key: 'account', label: 'Account', width: 'minmax(190px, 1fr)' },
  { key: 'status', label: 'Outcome', width: '100px' },
  { key: 'note', label: 'Reason', width: 'minmax(180px, 1.2fr)' },
  { key: 'attempts', label: 'Tries', width: '64px', align: 'right', mono: true },
  { key: 'finished', label: 'Finished', width: '100px', mono: true },
];

function firstFilter(run: BillingRun): OutcomeFilter {
  if (run.accounts.failed > 0) return 'failed';
  if (run.accounts.blocked > 0) return 'blocked';
  return 'all';
}

function RunOutcomes({
  run: initial,
  busy,
  onRetry,
}: {
  run: BillingRun;
  busy: boolean;
  onRetry: (run: BillingRun) => void;
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
    <section aria-label={`Run for ${run.runDate}`}>
      <header className="run-section-header">
        <h2>Outcomes</h2>
        <span className="bss-secondary">run {run.runDate}</span>
        {run.status === 'failed' && !run.isLatest && (
          <Button data-ui="ui.billingRuns.retry" disabled={busy} onClick={() => onRetry(run)}>
            Retry
          </Button>
        )}
      </header>
      <div className="run-outcomes-body">
        <details className="run-details">
          <summary>Run details</summary>
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
        </details>
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
          <TableHead columns={ACCOUNT_COLUMNS} density="compact" />
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
            count={(data) => data.items.length}
            rows={(data) => data.items}
            /* v8 ignore next -- @preserve unreachable: the outcome filter is asked of the server, and its empty page says "No <outcome> accounts" */
            onResetFilters={() => setFilter('all')}
          >
            {(rows, data) => (
              <>
                {rows.map((outcome) => (
                  <TableRow
                    key={outcome.billingAccount.id}
                    columns={ACCOUNT_COLUMNS}
                    density="compact"
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
                            {outcome.billingAccount.name ||
                              `Account ${shortId(outcome.billingAccount.id)}`}
                          </Link>{' '}
                          <Id id={outcome.billingAccount.id} prefix="BA-" />
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
                            Waits for the run of {outcome.waitsFor.runDate}
                          </span>
                        ) : (
                          '—'
                        ),
                      attempts: formatInt(outcome.attempts),
                      finished:
                        outcome.finishedAt === null
                          ? '—'
                          : clockTime(outcome.finishedAt, context.timeZone),
                    }}
                  />
                ))}
                <TableFooter
                  left={`Showing ${formatInt(rows.length)}${data.more ? ' (more follow)' : ''}`}
                />
              </>
            )}
          </ListState>
        </Panel>
      </div>
      <Note tone="info">
        Retry re-bills only the failed accounts for {run.runDate}. Billed accounts are not charged
        twice.
      </Note>
    </section>
  );
}
