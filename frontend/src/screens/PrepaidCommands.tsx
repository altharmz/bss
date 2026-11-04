import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useState } from 'react';
import {
  COMMAND_PAGE,
  COMMAND_STATES,
  loadCommand,
  loadCommandPage,
  retryCommand,
  type CommandState,
  type PrepaidCommand,
} from '../api/commands';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useRefreshNav, useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate, clockTime } from '../format/dates';
import { shortId } from '../format/ids';
import { formatCents, formatInt, toCents } from '../format/money';
import { Button, Dialog, KeyValue, PageTitle, Panel, StatusPill, Segmented } from '../ui/kit';
import { Id } from '../ui/Id';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// The prepaid command journal (docs/decisions/frontend.md, docs/ui.md):
// the worker's queue behind prepaid orders, renewals and transfers, read from the
// internal API. The one write is Retry, for a command that failed technically; the server
// decides everything else (a 409 is shown as it is).

const COLUMNS: Column[] = [
  { key: 'id', label: 'Command', width: '104px', mono: true },
  { key: 'kind', label: 'Kind', width: '90px' },
  { key: 'account', label: 'Account', width: 'minmax(0, 1fr)' },
  { key: 'state', label: 'State', width: '140px' },
  { key: 'due', label: 'Due', width: '104px', mono: true },
  { key: 'attempts', label: 'Attempts', width: '84px', align: 'right', mono: true },
  { key: 'next', label: 'Next retry', width: '150px', mono: true },
  { key: 'result', label: 'Result', width: 'minmax(0, 1.2fr)' },
];

type StateFilter = CommandState | 'all';
const STATE_LABEL: Record<StateFilter, string> = {
  all: 'All',
  pending: 'Pending',
  completed: 'Completed',
  failedTechnical: 'Failed technically',
};

function when(instant: string | null, timeZone: string): string {
  return instant === null
    ? '—'
    : `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone)}`;
}

function resultOf(command: PrepaidCommand): string {
  if (command.error !== null) return command.error;
  if (command.outcome !== null) {
    return command.reason !== null ? `${command.outcome}: ${command.reason}` : command.outcome;
  }
  return '';
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The account the link from the subscriber card asks for (?account=<id>). */
function accountFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get('account');
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

export function PrepaidCommandsScreen() {
  useSetCrumbs([{ label: 'Prepaid commands' }], 'prepaidCommands');
  const context = useSystemContext();
  const [state, setState] = useState<StateFilter>('all');
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<PrepaidCommand | null>(null);
  const filter = { state: state === 'all' ? null : state, account };
  const list = useResource(`prepaid-commands:${state}:${account ?? ''}:${offset}`, (signal) =>
    loadCommandPage(filter, offset, signal),
  );
  const refreshNav = useRefreshNav();

  const change = (next: StateFilter) => {
    setState(next);
    setOffset(0);
  };
  const dropAccount = () => {
    window.history.replaceState(null, '', '/prepaid-commands');
    setAccount(null);
    setOffset(0);
  };
  const name =
    list.status === 'ok'
      ? (list.data.items.find((c) => c.billingAccount.id === account)?.billingAccount.name ?? null)
      : null;
  const reset = () => {
    setState('all');
    if (account !== null) dropAccount();
    setOffset(0);
  };

  return (
    <div className="bss-screen" data-ui="ui.prepaidCommands">
      <div className="bss-title-row">
        <PageTitle title="Prepaid commands" />
        <span className="bss-caption">
          Orders, renewals and transfers the billing worker carries out
        </span>
      </div>
      <div className="bss-toolbar">
        <Segmented
          label="State"
          value={state}
          onChange={change}
          options={(['all', ...COMMAND_STATES] as const).map((key) => ({
            key,
            label: STATE_LABEL[key],
          }))}
        />
        {account !== null && (
          <span className="bss-run-tag" data-ui="ui.prepaidCommands.accountChip">
            Account {name ?? <Id id={account} prefix="BA-" />}{' '}
            <button
              type="button"
              className="bss-link"
              aria-label="Remove the account filter"
              onClick={dropAccount}
            >
              ×
            </button>
          </span>
        )}
      </div>
      <Panel>
        <TableHead columns={COLUMNS} density="dense" />
        <ListState
          resource={list}
          columns={COLUMNS}
          errorTitle="Failed to load prepaid commands"
          empty={{
            title: 'No prepaid commands',
            text: 'Prepaid orders, renewals and transfers appear here once they are accepted.',
          }}
          total={(data) => data.total}
          rows={(data) => data.items}
          onResetFilters={reset}
        >
          {(rows, data) => (
            <>
              {rows.map((command) => (
                <TableRow
                  key={command.id}
                  columns={COLUMNS}
                  density="dense"
                  onClick={() => setOpen(command)}
                  cells={{
                    id: <Id id={command.id} prefix="CMD-" />,
                    kind: command.kind,
                    account: (
                      <span className="ellipsis" onClick={(event) => event.stopPropagation()}>
                        <Link
                          className="bss-link bss-mono"
                          href={hrefOf({
                            name: 'subscriber',
                            accountId: command.billingAccount.id,
                          })}
                        >
                          {command.billingAccount.name ?? 'Account'}
                        </Link>{' '}
                        <Id id={command.billingAccount.id} prefix="BA-" />
                      </span>
                    ),
                    state: <StatusPill kind="command" status={command.state} />,
                    due: command.dueDate,
                    attempts: formatInt(command.attempts),
                    next: (
                      <span className="bss-text-2">
                        {when(command.nextRetryAt, context.timeZone)}
                      </span>
                    ),
                    result: (
                      <span className="ellipsis" title={resultOf(command)}>
                        {resultOf(command)}
                      </span>
                    ),
                  }}
                />
              ))}
              <TableFooter
                left={`Showing ${formatInt(offset + 1)}–${formatInt(offset + rows.length)} of ${formatInt(data.total)}`}
                right={
                  <span className="bss-toolbar">
                    <Button
                      small
                      data-ui="ui.prepaidCommands.previous"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - COMMAND_PAGE))}
                    >
                      Previous
                    </Button>
                    <Button
                      small
                      data-ui="ui.prepaidCommands.next"
                      disabled={offset + COMMAND_PAGE >= data.total}
                      onClick={() => setOffset(offset + COMMAND_PAGE)}
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
      {open !== null && (
        <CommandModal
          initial={open}
          onChanged={() => {
            list.retry();
            refreshNav();
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

function CommandModal({
  initial,
  onChanged,
  onClose,
}: {
  initial: PrepaidCommand;
  onChanged: () => void;
  onClose: () => void;
}) {
  const context = useSystemContext();
  const fresh = useResource(`prepaid-command:${initial.id}`, (signal) =>
    loadCommand(initial.id, signal),
  );
  const command = fresh.status === 'ok' ? fresh.data : initial;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const card = hrefOf({ name: 'subscriber', accountId: command.billingAccount.id });

  async function retry() {
    setBusy(true);
    setProblem(null);
    try {
      await retryCommand(command.id);
      fresh.retry();
      onChanged();
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={`Command ${shortId(command.id)}`}
      context={
        <>
          <StatusPill kind="command" status={command.state} /> {command.kind}
        </>
      }
      size="M"
      ui="ui.prepaidCommands.detail"
      onClose={onClose}
      footer={
        <>
          {command.state === 'failedTechnical' && (
            <Button
              variant="primary"
              data-ui="ui.prepaidCommands.retry"
              disabled={busy}
              onClick={() => void retry()}
            >
              Retry
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="bss-modal-body">
        {fresh.status === 'error' && (
          <Note tone="negative">
            Failed to refresh the command ({fresh.error.message}); showing the list's copy.
          </Note>
        )}
        {problem !== null && <Note tone="negative">{problem}</Note>}
        <KeyValue
          items={[
            ['Id', <Id id={command.id} prefix="CMD-" />],
            ['Kind', command.kind],
            ['State', <StatusPill kind="command" status={command.state} />],
            [
              'Account',
              <>
                <Link className="bss-link" href={card}>
                  {command.billingAccount.name ?? 'Account'}
                </Link>{' '}
                <Id id={command.billingAccount.id} prefix="BA-" />
              </>,
            ],
            ['Due date', <span className="bss-mono">{command.dueDate}</span>],
            [
              'Created',
              <span className="bss-mono">{when(command.createdAt, context.timeZone)}</span>,
            ],
            ['Attempts', <span className="bss-mono">{formatInt(command.attempts)}</span>],
            [
              'Last attempt',
              <span className="bss-mono">{when(command.attemptedAt, context.timeZone)}</span>,
            ],
            [
              'Next retry',
              <span className="bss-mono">{when(command.nextRetryAt, context.timeZone)}</span>,
            ],
            ['Outcome', command.outcome ?? '—'],
            ['Reason', command.reason ?? '—'],
            [
              'Error',
              <span className={command.error !== null ? 'bss-run-error inline' : undefined}>
                {command.error ?? '—'}
              </span>,
            ],
            [
              'Order',
              command.productOrder === null ? (
                '—'
              ) : (
                <Id id={command.productOrder.id} prefix="ORD-" />
              ),
            ],
            [
              'Products',
              command.products.length === 0 ? (
                '—'
              ) : (
                <>
                  {command.products.map((product) => (
                    <Id key={product.id} id={product.id} prefix="PRD-" />
                  ))}
                </>
              ),
            ],
            [
              'Billing run',
              <span className="bss-mono">
                {command.billingRun === null ? '—' : command.billingRun.runDate}
              </span>,
            ],
            [
              'Bills',
              command.bills.length === 0 ? (
                '—'
              ) : (
                <>
                  {command.bills.map((bill) => (
                    <Id key={bill.id} id={bill.id} prefix="BL-" />
                  ))}
                </>
              ),
            ],
          ]}
        />
        {command.needs_topup !== null && (
          <div className="bss-run-note warn" data-ui="ui.prepaidCommands.needsTopup" role="status">
            Needs a top-up: requires {formatCents(toCents(command.needs_topup.required))}, available{' '}
            {formatCents(toCents(command.needs_topup.available))}, short{' '}
            {formatCents(toCents(command.needs_topup.shortfall))} (seen{' '}
            {when(command.needs_topup.observedAt, context.timeZone)}).{' '}
            <Link className="bss-link" href={card}>
              Open the card to top up
            </Link>
          </div>
        )}
      </div>
    </Dialog>
  );
}
