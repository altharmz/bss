import { useWriteLock, writeLock } from '../api/WriteLock';
import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useRef, useState, type ReactNode } from 'react';
import type { PrepaidBalance } from '../api/bss';
import { explain } from '../api/client';
import { uncertain } from './submissionKey';
import {
  cancelAction,
  editNote,
  loadBalanceActions,
  removeAction,
  type BalanceAction,
} from '../api/history';
import { useResource, type ResourceWithRetry } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate, clockTime } from '../format/dates';
import { shortId } from '../format/ids';
import { formatCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Button, Modal, Panel, StatusPill, TextInput, useToast } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';

// The prepaid card's Balance actions panel: TMF654 balanceActionHistory, read-only
// (docs/decisions/frontend.md, slice 2 of docs/next-slices.md); the detail dialog carries
// the cancel, release, remove and edit-note writes (slice 3). Transfers the account
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

function ActionStatusPill({ action }: { action: BalanceAction }) {
  return <StatusPill kind="balanceAction" status={action.status} label={statusLabel(action)} />;
}

/** The panel needs the bucket id the card already loaded; until then (or if that failed)
 * it says so in place of the list. */
export function BalanceActions({
  balance,
  accountId,
  version,
  onOpen,
}: {
  balance: ResourceWithRetry<PrepaidBalance | null>;
  accountId: string;
  version: number;
  /** The card owns the selected action dialog across list reloads. */
  onOpen: (id: string) => void;
}) {
  if (balance.status === 'ok' && balance.data !== null) {
    return (
      <ActionsPanel
        accountId={accountId}
        bucketId={balance.data.bucketId}
        version={version}
        onOpen={onOpen}
      />
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
  onOpen,
}: {
  accountId: string;
  bucketId: string;
  version: number;
  onOpen: (id: string) => void;
}) {
  const { timeZone } = useSystemContext();
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
                onClick={() => onOpen(a.id)}
                cells={{
                  requested: when(a.requestedDate, timeZone),
                  action: actionLabel(a),
                  amount:
                    a.kind === 'reservation' ? (
                      <span className="bss-secondary">{formatCents(a.cents)}</span>
                    ) : (
                      <span className="amount-strong">
                        <Amount cents={a.cents} signed />
                      </span>
                    ),
                  status: <ActionStatusPill action={a} />,
                  note: <span className="bss-secondary">{a.reason ?? a.description ?? ''}</span>,
                }}
              />
            ))
          }
        </ListState>
      </div>
    </Panel>
  );
}

function when(instant: string, timeZone: string): string {
  return `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone).slice(0, 5)}`;
}

/** The Balance action dialog: the card's panel and the Balances screen both open it. `load`
 * fetches the entry (the history for the card, the kind's GET on the Balances screen). */
export function ActionDialog({
  id,
  load,
  onClose,
  onChanged,
}: {
  id: string;
  load: (signal: AbortSignal) => Promise<BalanceAction>;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { timeZone } = useSystemContext();
  const detail = useResource(`balance-action:${id}`, load);
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
      onSubmit={submitDialogPrimary}
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
          <>
            <dl className="bss-details">
              {rows.map(([label, value]) => (
                <div className="detail-contents" key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            <ActionWrites action={detail.data} onChanged={onChanged} />
          </>
        )}
      </div>
    </Modal>
  );
}

type Pending = 'cancel' | 'remove' | null;

/** The writes of one balance action (docs/next-slices-design.md section 3). The UI offers
 * what the entry's kind and status allow and shows the server's refusal in place. */
function ActionWrites({ action, onChanged }: { action: BalanceAction; onChanged: () => void }) {
  const toast = useToast();
  const [pending, setPending] = useState<Pending>(null);
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState(action.reason ?? '');
  const [description, setDescription] = useState(action.description ?? '');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);
  const running = useRef(false);
  const { begin } = useWriteLock();

  const { kind, status } = action;
  const amount = formatCents(Math.abs(action.cents));
  const cancel =
    status === 'completed' && kind === 'topup'
      ? {
          button: 'Cancel top-up',
          ui: 'ui.subscriber.prepaid.cancelTopup',
          text: `Takes ${amount} back off the balance. Only possible while none of it was spent or held. The payment record stays.`,
          done: 'Top-up cancelled',
        }
      : status === 'completed' && kind === 'adjustment'
        ? {
            button: 'Cancel adjustment',
            ui: 'ui.subscriber.prepaid.cancelAdjustment',
            text: `Reverses this ${action.cents < 0 ? 'debit' : 'credit'} of ${amount}.`,
            done: 'Adjustment cancelled',
          }
        : status === 'created' && kind === 'reservation'
          ? {
              button: 'Release hold',
              ui: 'ui.subscriber.prepaid.releaseHold',
              text: `Makes ${amount} available again.`,
              done: 'Hold released',
            }
          : null;
  const removable = status === 'failed';
  const editable = !removable && kind !== 'transfer';

  async function run(write: () => Promise<void>, message: string) {
    if (running.current) return;
    running.current = true;
    const release = begin();
    const before = writeLock.sent();
    setBusy(true);
    setProblem(null);
    setUnsure(false);
    try {
      await write();
      if (writeLock.sent() === before) {
        setProblem('Not sent. Try again.');
        return;
      }
      toast(message);
      onChanged();
    } catch (error) {
      setUnsure(uncertain(error));
      setProblem(
        uncertain(error)
          ? `${explain(error)}. Unsure whether it was saved: Refresh to check.`
          : explain(error),
      );
    } finally {
      release();
      running.current = false;
      setBusy(false);
    }
  }

  const note = () => {
    const changed: { reason?: string | null; description?: string | null } = {};
    if (reason.trim() !== (action.reason ?? '')) changed.reason = reason.trim() || null;
    if (description.trim() !== (action.description ?? ''))
      changed.description = description.trim() || null;
    return changed;
  };
  const changed = note();

  if (cancel === null && !removable && !editable) return null;
  return (
    <section>
      {problem !== null && (
        <Note tone="negative">
          {problem}{' '}
          {unsure && (
            <Button small onClick={onChanged}>
              Refresh
            </Button>
          )}
        </Note>
      )}
      {pending !== null ? (
        <span className="bss-confirm">
          <span className="ask">
            {pending === 'cancel'
              ? (cancel?.text ?? '')
              : 'Deletes this refused request. It moved no money.'}
          </span>
          <Button
            variant="danger"
            small
            disabled={busy}
            onClick={() =>
              void (pending === 'cancel' && cancel !== null
                ? run(() => cancelAction(kind, action.id, action.reason), cancel.done)
                : run(() => removeAction(kind, action.id), 'Refused request removed'))
            }
          >
            Yes
          </Button>
          <Button small disabled={busy} onClick={() => setPending(null)}>
            No
          </Button>
        </span>
      ) : (
        <div className="history-actions">
          {cancel !== null && (
            <Button
              variant="quiet"
              tone="negative"
              small
              data-ui={cancel.ui}
              onClick={() => setPending('cancel')}
            >
              {cancel.button}
            </Button>
          )}
          {removable && (
            <Button
              variant="quiet"
              tone="negative"
              small
              data-ui="ui.subscriber.prepaid.removeFailed"
              onClick={() => setPending('remove')}
            >
              Remove
            </Button>
          )}
          {editable && !editing && (
            <Button small data-ui="ui.subscriber.prepaid.editNote" onClick={() => setEditing(true)}>
              Edit note
            </Button>
          )}
        </div>
      )}
      {editable && editing && (
        <div data-ui="ui.subscriber.prepaid.editNoteForm">
          <div className="bss-step-head">Reason</div>
          <TextInput
            aria-label="Reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <div className="bss-step-head">Description</div>
          <TextInput
            aria-label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <div className="history-detail-gap">
            <Button
              variant="primary"
              small
              disabled={
                busy ||
                Object.keys(changed).length === 0 ||
                (kind === 'reservation' && reason.trim() === '')
              }
              onClick={() =>
                void run(
                  () => editNote(kind, action.id, status, reason.trim() || null, changed),
                  'Note saved',
                )
              }
            >
              Save note
            </Button>{' '}
            <Button small disabled={busy} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
