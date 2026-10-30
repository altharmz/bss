import { Note } from '../ui/Note';
import { useEffect, useEffectEvent, useState } from 'react';
import {
  loadCommand,
  loadFailedCommands,
  retryCommand,
  type PrepaidCommand,
} from '../api/commands';
import { ApiError, explain } from '../api/client';
import { usePolled } from '../api/usePolled';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { formatCents, toCents } from '../format/money';
import { Button, Panel } from '../ui/kit';
import { ErrorBlock } from '../ui/states';
import { POLL_MS, type Watched } from './watched';
import { WatchProgress } from './WatchProgress';

// A prepaid order is accepted before it is done: the billing worker completes it, and the
// answer to the order POST is `inProgress` (docs/decisions/prepaid.md P3c-P3g). The order's id
// is its command's id, so the command says where it stands, in three kinds kept apart:
// still waiting for the worker (polled), needing a top-up (a funding wait that ends when the
// account is funded; not a fault) and a technical failure (an operator can retry it).

const OUTCOME: Record<string, string> = {
  funded: 'completed',
  terminated: 'ended',
  cancelled: 'cancelled',
};

/** "2026-10-24 14:05" in the business time zone. */
function when(instant: string, timeZone: string): string {
  return `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone)}`;
}

const asHttp = (error: unknown, status: number) =>
  error instanceof ApiError && error.status === status;

function TechnicalFailure({
  command,
  label,
  onRetried,
}: {
  command: PrepaidCommand;
  label: string;
  onRetried: () => void;
}) {
  const context = useSystemContext();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  async function retry() {
    setBusy(true);
    setProblem(null);
    try {
      await retryCommand(command.id);
      onRetried();
    } catch (error) {
      // 409: it was retried (or finished) since this was loaded; look again instead
      if (asHttp(error, 409)) onRetried();
      else setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <strong>{label}</strong> hit a technical problem: {command.error ?? 'unknown error'}.{' '}
      {command.nextRetryAt !== null
        ? `The worker retries by itself after ${when(command.nextRetryAt, context.timeZone)}.`
        : 'The worker retries by itself.'}{' '}
      No funds are lost.{' '}
      <Button small disabled={busy} onClick={() => void retry()}>
        Retry now
      </Button>
      {problem !== null && <Note tone="negative">{problem}</Note>}
    </>
  );
}

/** Follows an accepted prepaid order through its command (see the file comment). */
export function OrderWatch({
  watched,
  accountId,
  onTopUp,
  onSettled,
  onDismiss,
}: {
  watched: Watched;
  accountId?: string;
  onTopUp?: (amount: string) => void;
  onSettled: (message: string) => void;
  onDismiss: () => void;
}) {
  const context = useSystemContext();
  const command = usePolled(
    `order:${watched.id}`,
    (signal) => loadCommand(watched.id, signal),
    (c) => c.state === 'pending' && c.needs_topup === null && c.dueDate <= context.businessDate,
    POLL_MS,
    watched.until,
  );
  const data = command.status === 'ok' ? command.data : null;
  const outcome = data?.state === 'completed' ? (data.outcome ?? 'funded') : null;
  const reason = data?.reason ?? null;
  const topUpAmount = data?.needs_topup
    ? formatCents(toCents(data.needs_topup.shortfall)).replace(/,/g, '')
    : null;
  const settle = useEffectEvent(onSettled);
  useEffect(() => {
    if (outcome === null) return;
    const word = OUTCOME[outcome] ?? outcome;
    settle(`${watched.label} ${word}${outcome !== 'funded' && reason ? `: ${reason}` : ''}`);
  }, [outcome, reason, watched.label]);

  const dismiss = (
    <Button small onClick={onDismiss}>
      Dismiss
    </Button>
  );
  const check = (
    <Button small onClick={command.refresh}>
      Check now
    </Button>
  );
  return (
    <div className="subscriber-watch" data-ui="ui.subscriber.prepaid.orderWatch" role="status">
      <WatchProgress
        lastCheckedAt={command.lastCheckedAt}
        phase={
          outcome !== null
            ? 'completed'
            : data?.state === 'failedTechnical'
              ? 'failed'
              : data?.needs_topup != null
                ? 'funding'
                : data && data.dueDate > context.businessDate
                  ? 'scheduled'
                  : 'waiting'
        }
      >
        {outcome !== null ? (
          <>
            {watched.label} {OUTCOME[outcome] ?? outcome}
          </>
        ) : command.status === 'error' || command.refreshError !== null ? (
          <>
            Could not check {watched.label} (
            {explain(command.status === 'error' ? command.error : command.refreshError)}).{' '}
            <Button small onClick={command.retry}>
              Check again
            </Button>{' '}
            {dismiss}
          </>
        ) : data === null ? (
          <>{watched.label} accepted, checking…</>
        ) : data.state === 'failedTechnical' ? (
          <>
            <TechnicalFailure command={data} label={watched.label} onRetried={command.refresh} />{' '}
            {dismiss}
          </>
        ) : data.needs_topup !== null ? (
          <>
            <strong>{watched.label}</strong> needs a top-up: it requires{' '}
            {formatCents(toCents(data.needs_topup.required))}, the account has{' '}
            {formatCents(toCents(data.needs_topup.available))} (short{' '}
            {formatCents(toCents(data.needs_topup.shortfall))}). It completes by itself once the
            account is funded.{' '}
            {onTopUp && topUpAmount !== null && (
              <Button small onClick={() => onTopUp(topUpAmount)}>
                Top up
              </Button>
            )}{' '}
            {check} {dismiss}
          </>
        ) : data.dueDate > context.businessDate ? (
          <>
            <strong>{watched.label}</strong> is scheduled for {data.dueDate} and starts then.{' '}
            {dismiss}
          </>
        ) : command.capped ? (
          <>
            {watched.label} is still waiting for the billing worker. It completes on the
            worker&apos;s next wake-up. Still running. It continues in{' '}
            <a className="bss-link" href={`/orders?account=${accountId ?? data.billingAccount.id}`}>
              Orders
            </a>
            . {check} {dismiss}
          </>
        ) : (
          <>{watched.label} accepted, waiting for the billing worker…</>
        )}
      </WatchProgress>
    </div>
  );
}

/** Commands of this account that failed technically, whichever order, renewal or transfer
 * they belong to: shown only while there are some. */
export function FailedCommands({
  accountId,
  version,
  onRetried,
  onCount,
}: {
  accountId: string;
  version: number;
  onRetried: () => void;
  onCount?: (count: number | undefined) => void;
}) {
  const failed = useResource(`failed-commands:${accountId}:${version}`, (signal) =>
    loadFailedCommands(accountId, signal),
  );
  const count = failed.status === 'ok' ? failed.data.length : undefined;
  useEffect(() => {
    onCount?.(count);
  }, [count, onCount]);
  if (failed.status === 'loading') return null;
  if (failed.status === 'error') {
    return (
      <Panel title="Prepaid commands">
        <ErrorBlock
          title="Failed to load prepaid commands"
          error={failed.error}
          onRetry={failed.retry}
        />
      </Panel>
    );
  }
  if (failed.data.length === 0) return null;
  return (
    <Panel
      title="Needs attention"
      right={<span className="bss-panel-count">{failed.data.length}</span>}
    >
      <div data-ui="ui.subscriber.prepaid.failedCommands">
        {failed.data.map((command) => (
          <p key={command.id}>
            <TechnicalFailure
              command={command}
              label={`The ${command.kind} due ${command.dueDate}`}
              onRetried={onRetried}
            />
          </p>
        ))}
      </div>
    </Panel>
  );
}
