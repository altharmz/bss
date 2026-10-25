import { useState } from 'react';
import { loadBucketDetail, loadBucketRows, BUCKET_STATUSES, type BucketRow } from '../api/balances';
import {
  loadKindAction,
  loadKindActions,
  type ActionKind,
  type BalanceAction,
} from '../api/history';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate, clockTime } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { formatCents, formatInt } from '../format/money';
import { Modal, PageTitle, Panel, Pill, Segmented } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { actionLabel, ActionDialog, statusLabel } from './PrepaidHistory';
import './screens.css';

// The global Balances screen (docs/decisions/frontend.md, slice 22): TMF654 reads only.
// Buckets are joined with the accumulated balance of their account; the four action tabs
// are the kinds' lists. A row opens the kind's GET detail with the Balance action dialog,
// its actions included. Postpaid accounts have no bucket and never appear.

type Tab = 'buckets' | 'topups' | 'adjustments' | 'holds' | 'transfers';

const KIND_OF: Record<Exclude<Tab, 'buckets'>, ActionKind> = {
  topups: 'topup',
  adjustments: 'adjustment',
  holds: 'reservation',
  transfers: 'transfer',
};

/** TMF654 ActionStatusType: what the four action lists can hold. */
const ACTION_STATUSES = ['created', 'completed', 'cancelled', 'failed'] as const;

const BUCKET_COLUMNS: Column[] = [
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1.2fr)' },
  { key: 'remaining', label: 'Remaining', width: '130px', align: 'right', mono: true },
  { key: 'reserved', label: 'Reserved', width: '130px', align: 'right', mono: true },
  { key: 'total', label: 'Total', width: '130px', align: 'right', mono: true },
  { key: 'status', label: 'Status', width: '120px' },
];

const ACTION_COLUMNS: Column[] = [
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1fr)' },
  { key: 'kind', label: 'Kind', width: 'minmax(0, 1.2fr)' },
  { key: 'requested', label: 'Requested', width: '170px', mono: true },
  { key: 'amount', label: 'Amount', width: '130px', align: 'right', mono: true },
  { key: 'status', label: 'Status', width: '120px' },
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function accountFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get('account');
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

function stamp(instant: string, timeZone: string): string {
  return `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone).slice(0, 5)}`;
}

function AccountLink({ id, name }: { id: string; name: string | null }) {
  return (
    <span className="ellipsis" onClick={(e) => e.stopPropagation()}>
      <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId: id })}>
        {name ?? accountLabel(id)}
      </Link>
    </span>
  );
}

function actionCells(action: BalanceAction, timeZone: string) {
  return {
    account: <AccountLink id={action.sender.id} name={action.sender.name} />,
    kind: actionLabel(action),
    requested: stamp(action.requestedDate, timeZone),
    amount: formatCents(action.cents),
    status: <Pill status={action.status} label={statusLabel(action)} />,
  };
}

export function BalancesScreen() {
  useSetCrumbs([{ label: 'Balances' }], 'balances');
  const { timeZone } = useSystemContext();
  const [tab, setTab] = useState<Tab>('buckets');
  const [status, setStatus] = useState('');
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  const [version, setVersion] = useState(0);
  const [openBucket, setOpenBucket] = useState<BucketRow | null>(null);
  const [openAction, setOpenAction] = useState<{ id: string; kind: ActionKind } | null>(null);
  const filter = { account, status: status === '' ? null : status };
  const key = `${tab}:${account ?? ''}:${status}:${version}`;
  // each tab reads only its own list: the buckets tab makes no action request and vice versa
  const buckets = useResource(`balances-buckets:${key}`, (signal) =>
    tab === 'buckets' ? loadBucketRows(filter, signal) : Promise.resolve([] as BucketRow[]),
  );
  const actions = useResource(`balances-actions:${key}`, (signal) =>
    tab === 'buckets'
      ? Promise.resolve([] as BalanceAction[])
      : loadKindActions(KIND_OF[tab], filter, signal),
  );

  const dropAccount = () => {
    window.history.replaceState(null, '', '/balances');
    setAccount(null);
  };
  const reset = () => {
    setStatus('');
    if (account !== null) dropAccount();
  };
  const changeTab = (next: Tab) => {
    setTab(next);
    setStatus('');
  };
  const name =
    tab === 'buckets'
      ? buckets.status === 'ok'
        ? (buckets.data.find((r) => r.account.id === account)?.account.name ?? null)
        : null
      : actions.status === 'ok'
        ? (actions.data.find((a) => a.sender.id === account)?.sender.name ?? null)
        : null;
  const statusOptions = tab === 'buckets' ? BUCKET_STATUSES : ACTION_STATUSES;
  const shown = (rows: number, all: number) =>
    rows === all ? `${formatInt(rows)}` : `${formatInt(rows)} of ${formatInt(all)}`;

  return (
    <div className="bss-screen" data-ui="ui.balances">
      <div className="bss-title-row">
        <PageTitle title="Balances" />
        <span className="bss-caption">Prepaid funds and the operations on them (TMF654)</span>
      </div>
      <div className="bss-toolbar">
        <Segmented
          label="View"
          value={tab}
          onChange={changeTab}
          options={[
            { key: 'buckets', label: 'Buckets' },
            { key: 'topups', label: 'Top-ups' },
            { key: 'adjustments', label: 'Adjustments' },
            { key: 'holds', label: 'Holds' },
            { key: 'transfers', label: 'Transfers' },
          ]}
        />
        <select
          aria-label="Status"
          data-ui="ui.balances.status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">All statuses</option>
          {statusOptions.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {account !== null && (
          <span className="bss-run-tag" data-ui="ui.balances.accountChip">
            Account {name ?? accountLabel(account)}{' '}
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
        {tab === 'buckets' ? (
          <div data-ui="ui.balances.buckets">
            <TableHead columns={BUCKET_COLUMNS} />
            <ListState
              resource={buckets}
              columns={BUCKET_COLUMNS}
              errorTitle="Failed to load the buckets"
              empty={{ title: 'No buckets', text: 'Prepaid accounts appear here once enabled.' }}
              total={(data) => data.length}
              rows={(data) => data}
              onResetFilters={reset}
            >
              {(rows, data) => (
                <>
                  {rows.map((row) => (
                    <TableRow
                      key={row.bucketId}
                      columns={BUCKET_COLUMNS}
                      onClick={() => setOpenBucket(row)}
                      cells={{
                        account: <AccountLink id={row.account.id} name={row.account.name} />,
                        remaining: formatCents(row.remainingCents),
                        reserved: formatCents(row.reservedCents),
                        total: row.totalCents === null ? '—' : formatCents(row.totalCents),
                        status: <Pill status={row.status} />,
                      }}
                    />
                  ))}
                  <TableFooter left={`Showing ${shown(rows.length, data.length)}`} />
                </>
              )}
            </ListState>
          </div>
        ) : (
          <div data-ui={`ui.balances.${tab}`}>
            <TableHead columns={ACTION_COLUMNS} />
            <ListState
              resource={actions}
              columns={ACTION_COLUMNS}
              errorTitle="Failed to load the list"
              empty={{ title: 'Nothing here', text: 'No matching requests.' }}
              total={(data) => data.length}
              rows={(data) => data}
              onResetFilters={reset}
            >
              {(rows, data) => (
                <>
                  {rows.map((action) => (
                    <TableRow
                      key={action.id}
                      columns={ACTION_COLUMNS}
                      onClick={() => setOpenAction({ id: action.id, kind: KIND_OF[tab] })}
                      cells={actionCells(action, timeZone)}
                    />
                  ))}
                  <TableFooter left={`Showing ${shown(rows.length, data.length)}`} />
                </>
              )}
            </ListState>
          </div>
        )}
      </Panel>
      {openBucket !== null && (
        <BucketDialog
          key={`${openBucket.bucketId}:${version}`}
          row={openBucket}
          onClose={() => setOpenBucket(null)}
        />
      )}
      {openAction !== null && (
        <ActionDialog
          key={`${openAction.id}:${version}`}
          id={openAction.id}
          load={(signal) => loadKindAction(openAction.kind, openAction.id, signal)}
          onClose={() => setOpenAction(null)}
          onChanged={() => setVersion((v) => v + 1)}
        />
      )}
    </div>
  );
}

/** One bucket: GET bucket/{id} and the account's GET accumulatedBalance/{id}. */
function BucketDialog({ row, onClose }: { row: BucketRow; onClose: () => void }) {
  const { timeZone } = useSystemContext();
  const detail = useResource(`bucket:${row.bucketId}`, (signal) => loadBucketDetail(row, signal));
  return (
    <Modal title="Bucket" width={520} onClose={onClose} ui="ui.balances.bucketDetail">
      <div className="bss-modal-body">
        {detail.status === 'loading' && <SkeletonRows columns={BUCKET_COLUMNS} rows={3} />}
        {detail.status === 'error' && (
          <ErrorBlock
            title="Failed to load the bucket"
            error={detail.error}
            onRetry={detail.retry}
          />
        )}
        {detail.status === 'ok' && (
          <dl className="bss-details">
            <dt>Subscriber</dt>
            <dd>
              <AccountLink id={row.account.id} name={row.account.name} />
            </dd>
            <dt>Id</dt>
            <dd>{shortId(detail.data.bucketId)}</dd>
            <dt>Status</dt>
            <dd>
              <Pill status={detail.data.status} />
            </dd>
            <dt>Remaining</dt>
            <dd>{formatCents(detail.data.remainingCents)}</dd>
            <dt>Reserved</dt>
            <dd>{formatCents(detail.data.reservedCents)}</dd>
            <dt>Total</dt>
            <dd>{detail.data.totalCents === null ? '—' : formatCents(detail.data.totalCents)}</dd>
            {detail.data.expiry !== null && (
              <>
                <dt>Expires</dt>
                <dd>{stamp(detail.data.expiry, timeZone)}</dd>
              </>
            )}
          </dl>
        )}
      </div>
    </Modal>
  );
}
