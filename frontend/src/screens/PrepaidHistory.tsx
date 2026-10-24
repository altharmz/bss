import { useState, type ReactNode } from 'react';
import type { PrepaidBalance } from '../api/bss';
import { loadBalanceAction, loadBalanceActions, type BalanceAction } from '../api/history';
import { useResource, type ResourceWithRetry } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate, clockTime } from '../format/dates';
import { shortId } from '../format/ids';
import { formatCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Modal, Panel, Pill } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';

// The prepaid card's Balance actions panel: TMF654 balanceActionHistory, read-only
// (docs/decisions/frontend.md, slice 2 of docs/next-slices.md). Transfers the account
// received are found by the receiver bucket, the rest by the account.

const COLUMNS: Column[] = [
  { key: 'requested', label: 'Requested', width: '150px', mono: true },
  { key: 'action', label: 'Action', width: '1fr' },
  { key: 'amount', label: 'Amount', width: '116px', align: 'right', mono: true },
  { key: 'status', label: 'Status', width: '110px' },
  { key: 'note', label: 'Note', width: '1fr' },
];

const who = (account: { id: string; name: string | null } | null) =>
  account === null ? 'another account' : (account.name ?? shortId(account.id));

export function actionLabel(a: BalanceAction): string {
  switch (a.kind) {
    case 'topup':
      return 'Top-up';
    case 'adjustment':
      return a.cents < 0 ? 'Debit adjustment' : 'Credit adjustment';
    case 'reservation':
      return 'Hold';
    case 'transfer':
      return a.sent ? `Transfer to ${who(a.receiver)}` : `Transfer from ${who(a.sender)}`;
  }
}

/** The status as an operator reads it: a hold is Held or Released, anything else Cancelled. */
export function statusLabel(a: BalanceAction): string {
  switch (a.status) {
    case 'completed':
      return 'Completed';
    case 'created':
      return a.kind === 'reservation' ? 'Held' : 'Pending';
    case 'cancelled':
      return a.kind === 'reservation' ? 'Released' : 'Cancelled';
    case 'failed':
      return 'Refused';
    default:
      return a.status;
  }
}

function StatusPill({ action }: { action: BalanceAction }) {
  return <Pill status={action.status} label={statusLabel(action)} />;
}

/** The panel needs the bucket id the card already loaded; until then (or if that failed)
 * it says so in place of the list. */
export function BalanceActions({
  balance,
  accountId,
  version,
}: {
  balance: ResourceWithRetry<PrepaidBalance | null>;
  accountId: string;
  version: number;
}) {
  if (balance.status === 'ok' && balance.data !== null) {
    return (
      <ActionsPanel accountId={accountId} bucketId={balance.data.bucketId} version={version} />
    );
  }
  return (
    <Panel title="Balance actions" subtitle="TMF654 balanceActionHistory">
      {balance.status === 'error' ? (
        <ErrorBlock
          title="Failed to load the balance actions"
          error={balance.error}
          onRetry={balance.retry}
        />
      ) : (
        <SkeletonRows columns={COLUMNS} />
      )}
    </Panel>
  );
}

function ActionsPanel({
  accountId,
  bucketId,
  version,
}: {
  accountId: string;
  bucketId: string;
  version: number;
}) {
  const { timeZone } = useSystemContext();
  const [open, setOpen] = useState<BalanceAction | null>(null);
  const actions = useResource(`balance-actions:${accountId}:${bucketId}:${version}`, (signal) =>
    loadBalanceActions(accountId, bucketId, signal),
  );
  return (
    <Panel title="Balance actions" subtitle="TMF654 balanceActionHistory">
      <div data-ui="ui.subscriber.prepaid.history">
        <TableHead columns={COLUMNS} />
        <ListState
          resource={actions}
          columns={COLUMNS}
          errorTitle="Failed to load the balance actions"
          empty={{
            title: 'No balance actions',
            text: 'Top-ups, holds, adjustments and transfers appear here.',
          }}
          total={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {}}
        >
          {(rows) =>
            rows.map((a) => (
              <TableRow
                key={a.id}
                columns={COLUMNS}
                onClick={() => setOpen(a)}
                cells={{
                  requested: when(a.requestedDate, timeZone),
                  action: actionLabel(a),
                  amount:
                    a.kind === 'reservation' ? (
                      <span className="bss-secondary">{formatCents(a.cents)}</span>
                    ) : (
                      <Amount cents={a.cents} signed strong />
                    ),
                  status: <StatusPill action={a} />,
                  note: <span className="bss-secondary">{a.reason ?? a.description ?? ''}</span>,
                }}
              />
            ))
          }
        </ListState>
      </div>
      {open !== null && (
        <ActionDialog id={open.id} accountId={accountId} onClose={() => setOpen(null)} />
      )}
    </Panel>
  );
}

function when(instant: string, timeZone: string): string {
  return `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone).slice(0, 5)}`;
}

function ActionDialog({
  id,
  accountId,
  onClose,
}: {
  id: string;
  accountId: string;
  onClose: () => void;
}) {
  const { timeZone } = useSystemContext();
  const detail = useResource(`balance-action:${id}`, (signal) =>
    loadBalanceAction(id, accountId, signal),
  );
  const cards = (account: { id: string; name: string | null }) => (
    <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId: account.id })}>
      {who(account)}
    </Link>
  );
  const rows: [string, ReactNode][] = [];
  if (detail.status === 'ok') {
    const a = detail.data;
    rows.push(['Id', shortId(a.id)], ['Kind', a.kind], ['Status', statusLabel(a)]);
    rows.push(['Amount', formatCents(Math.abs(a.cents)) + (a.cents < 0 ? ' (debit)' : '')]);
    rows.push(['Requested', when(a.requestedDate, timeZone)]);
    if (a.confirmationDate) rows.push(['Confirmed', when(a.confirmationDate, timeZone)]);
    if (a.reason) rows.push(['Reason', a.reason]);
    if (a.description) rows.push(['Description', a.description]);
    if (a.paymentMethod) rows.push(['Payment method', a.paymentMethod]);
    if (a.expiry) rows.push(['Expires', when(a.expiry, timeZone)]);
    if (a.kind === 'transfer') {
      rows.push(['From', cards(a.sender)]);
      if (a.receiver) rows.push(['To', cards(a.receiver)]);
      if (a.transferCostCents !== null) {
        rows.push(['Transfer cost', formatCents(a.transferCostCents)]);
        rows.push(['Paid by', a.costOwner ?? '—']);
      }
    }
    if (a.product) rows.push(['Product', a.product.name ?? shortId(a.product.id)]);
  }
  return (
    <Modal
      title="Balance action"
      width={560}
      onClose={onClose}
      ui="ui.subscriber.prepaid.historyDetail"
    >
      <div className="bss-modal-body">
        {detail.status === 'loading' && <SkeletonRows columns={COLUMNS} rows={4} />}
        {detail.status === 'error' && (
          <ErrorBlock
            title="Failed to load the balance action"
            error={detail.error}
            onRetry={detail.retry}
          />
        )}
        {detail.status === 'ok' && (
          <dl className="bss-details">
            {rows.map(([label, value]) => (
              <div key={label} style={{ display: 'contents' }}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </Modal>
  );
}
