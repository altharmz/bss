import { useState } from 'react';
import {
  cancelOrder,
  loadAccount,
  patchOrderDetails,
  removeOrder,
  submitDraft,
  type OrderDetails,
} from '../api/bss';
import { explain } from '../api/client';
import {
  findOrderCommand,
  loadCancellations,
  loadOrderPage,
  ORDER_PAGE,
  ORDER_STATES,
  type Order,
  type OrderItem,
  type OrderState,
} from '../api/orders';
import { useResource } from '../api/useResource';
import { useRefreshNav, useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate, clockTime } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { formatAmount, formatInt } from '../format/money';
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
import { AddProductModal, type DraftToEdit } from './AddProduct';
import { OrderWatch } from './PrepaidOrderWatch';
import { watch, type Watched } from './watched';
import { ErrorBlock } from '../ui/states';
import { ListState } from '../ui/states';
import { uncertain } from './submissionKey';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// The global Orders list (docs/decisions/frontend.md, docs/next-slices-design.md section 9):
// TMF622 reads, and (slice 10) the order dialog's writes: cancel, edit details, drafts.

const COLUMNS: Column[] = [
  { key: 'id', label: 'Order', width: '104px', mono: true },
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1fr)' },
  { key: 'state', label: 'State', width: '120px' },
  { key: 'items', label: 'Items', width: 'minmax(0, 1.4fr)' },
  { key: 'start', label: 'Start', width: '104px', mono: true },
  { key: 'created', label: 'Created', width: '104px', mono: true },
  { key: 'ended', label: 'Completed / cancelled', width: '150px', mono: true },
];

type StateFilter = OrderState | 'all';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The account the subscriber card's link asks for (?account=<id>). */
function accountFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get('account');
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

function day(instant: string | undefined, timeZone: string): string {
  return instant === undefined ? '—' : businessDate(instant, timeZone);
}

/** "add Basic 10", "delete product 1F3A9C2B". */
function itemText(item: OrderItem): string {
  const target =
    item.productOffering?.name ??
    (item.product?.id !== undefined ? `product ${shortId(item.product.id)}` : '');
  return `${item.action} ${target}`.trim();
}

function itemSummary(order: Order): string {
  return (order.productOrderItem ?? []).map(itemText).join(', ');
}

export function OrdersScreen() {
  useSetCrumbs([{ label: 'Orders' }], 'orders');
  const context = useSystemContext();
  const [state, setState] = useState<StateFilter>('all');
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<Order | null>(null);
  const [watched, setWatched] = useState<Watched | null>(null);
  const toast = useToast();
  const refreshNav = useRefreshNav();
  const list = useResource(`orders:${state}:${account ?? ''}:${offset}`, (signal) =>
    loadOrderPage({ state: state === 'all' ? null : state, account }, offset, signal),
  );

  const dropAccount = () => {
    window.history.replaceState(null, '', '/orders');
    setAccount(null);
    setOffset(0);
  };
  const name =
    list.status === 'ok'
      ? (list.data.items.find((o) => o.billingAccount?.id === account)?.billingAccount?.name ??
        null)
      : null;

  return (
    <div className="bss-screen" data-ui="ui.orders">
      <div className="bss-title-row">
        <PageTitle title="Orders" />
        <span className="bss-caption">Every product order, oldest first</span>
      </div>
      <div className="bss-toolbar">
        <Segmented
          label="State"
          value={state}
          onChange={(next) => {
            setState(next);
            setOffset(0);
          }}
          options={(['all', ...ORDER_STATES] as const).map((key) => ({
            key,
            label: key === 'all' ? 'All' : key,
          }))}
        />
        {account !== null && (
          <span className="bss-run-tag" data-ui="ui.orders.accountChip">
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
      {watched !== null && (
        <OrderWatch
          watched={watched}
          onDismiss={() => setWatched(null)}
          onSettled={(message) => {
            setWatched(null);
            toast(message);
            list.retry();
            refreshNav();
          }}
        />
      )}
      <Panel>
        <TableHead columns={COLUMNS} />
        <ListState
          resource={list}
          columns={COLUMNS}
          errorTitle="Failed to load orders"
          empty={{
            title: 'No orders',
            text: 'Orders appear here once a product is added, changed or ended.',
          }}
          total={(data) => data.total}
          rows={(data) => data.items}
          onResetFilters={() => {
            setState('all');
            if (account !== null) dropAccount();
            setOffset(0);
          }}
        >
          {(rows, data) => (
            <>
              {rows.map((order) => (
                <TableRow
                  key={order.id}
                  columns={COLUMNS}
                  onClick={() => setOpen(order)}
                  cells={{
                    id: <span title={order.id}>{shortId(order.id ?? '')}</span>,
                    account:
                      order.billingAccount?.id !== undefined ? (
                        <span className="ellipsis" onClick={(event) => event.stopPropagation()}>
                          <Link
                            className="bss-link"
                            href={hrefOf({
                              name: 'subscriber',
                              accountId: order.billingAccount.id,
                            })}
                          >
                            {order.billingAccount.name ?? accountLabel(order.billingAccount.id)}
                          </Link>
                        </span>
                      ) : (
                        '—'
                      ),
                    state: (
                      <StatusPill
                        kind="order"
                        status={order.state ?? ''}
                        label={order.state ?? '—'}
                      />
                    ),
                    items: (
                      <span className="ellipsis" title={itemSummary(order)}>
                        {itemSummary(order)}
                      </span>
                    ),
                    start: day(order.requestedStartDate, context.timeZone),
                    created: day(order.creationDate, context.timeZone),
                    ended: day(order.completionDate ?? order.cancellationDate, context.timeZone),
                  }}
                />
              ))}
              <TableFooter
                left={`Showing ${formatInt(offset + 1)}–${formatInt(offset + rows.length)} of ${formatInt(data.total)}`}
                right={
                  <span className="bss-toolbar">
                    <Button
                      small
                      data-ui="ui.orders.previous"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - ORDER_PAGE))}
                    >
                      Previous
                    </Button>
                    <Button
                      small
                      data-ui="ui.orders.next"
                      disabled={offset + ORDER_PAGE >= data.total}
                      onClick={() => setOffset(offset + ORDER_PAGE)}
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
        <OrderModal
          order={open}
          onClose={() => setOpen(null)}
          onChanged={() => {
            list.retry();
            refreshNav();
          }}
          onWatch={(id, label) => setWatched(watch('order', id, label))}
        />
      )}
    </div>
  );
}

function priceText(item: OrderItem): string {
  const price = item.itemTotalPrice?.[0]?.price?.taxIncludedAmount;
  return price?.value === undefined
    ? '—'
    : `${formatAmount(price.value)} ${price.unit ?? ''}`.trim();
}

type Panel = 'cancel' | 'details' | 'delete' | null;
const PRIORITIES = ['0', '1', '2', '3', '4'];

/** The draft's add item as Add product reopens it, or null for a draft of another shape
 * (those are edited through the API). */
function draftToEdit(order: Order, timeZone: string): DraftToEdit | null {
  const items = order.productOrderItem ?? [];
  const item = items[0];
  if (
    order.state !== 'draft' ||
    items.length !== 1 ||
    item?.action !== 'add' ||
    item.productOffering?.id === undefined ||
    order.billingAccount?.id === undefined ||
    order.id === undefined
  )
    return null;
  const resource = (item.product as { realizingResource?: { id?: string }[] } | undefined)
    ?.realizingResource?.[0];
  return {
    orderId: order.id,
    itemId: item.id ?? '1',
    offeringId: item.productOffering.id,
    start:
      order.requestedStartDate === undefined
        ? null
        : businessDate(order.requestedStartDate, timeZone),
    numberId: resource?.id ?? '',
  };
}

function EditDraft({
  order,
  draft,
  onClose,
  onSaved,
}: {
  order: Order;
  draft: DraftToEdit;
  onClose: () => void;
  onSaved: () => void;
}) {
  const accountId = order.billingAccount?.id ?? '';
  const account = useResource(`draft-account:${accountId}`, (signal) =>
    loadAccount(accountId, signal),
  );
  if (account.status === 'ok')
    return (
      <AddProductModal
        account={account.data}
        activeOfferingIds={new Set()}
        draft={draft}
        onClose={onClose}
        onOrdered={() => {}}
        onDraftSaved={onSaved}
      />
    );
  return (
    <Modal
      title="Edit draft"
      width={560}
      onClose={onClose}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="bss-modal-body">
        {account.status === 'error' ? (
          <ErrorBlock
            title="Failed to load the account"
            error={account.error}
            onRetry={account.retry}
          />
        ) : (
          <div className="bss-caption">Loading…</div>
        )}
      </div>
    </Modal>
  );
}

function OrderModal({
  order: initial,
  onClose,
  onChanged,
  onWatch,
}: {
  order: Order;
  onClose: () => void;
  /** An order changed: the list and the sidebar count reload. */
  onChanged: () => void;
  onWatch: (orderId: string, label: string) => void;
}) {
  const context = useSystemContext();
  const toast = useToast();
  const [order, setOrder] = useState(initial);
  const [panel, setPanel] = useState<Panel>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);
  const [reason, setReason] = useState('');
  const [description, setDescription] = useState(order.description ?? '');
  const [category, setCategory] = useState(order.category ?? '');
  const [priority, setPriority] = useState(order.priority ?? '');
  const orderId = order.id ?? '';
  const accountId = order.billingAccount?.id;
  const state = order.state;
  const cancellations = useResource(`order-cancellations:${orderId}`, (signal) =>
    loadCancellations(orderId, signal),
  );
  // a prepaid order the worker has not finished: link to its command
  const command = useResource(`order-command:${orderId}`, (signal) =>
    order.state === 'inProgress' && accountId !== undefined
      ? findOrderCommand(orderId, accountId, signal)
      : Promise.resolve(null),
  );
  const when = (instant: string | undefined) =>
    instant === undefined
      ? '—'
      : `${businessDate(instant, context.timeZone)} ${clockTime(instant, context.timeZone)}`;

  const draft = draftToEdit(order, context.timeZone);
  const canCancel = state === 'completed' || state === 'draft' || state === 'inProgress';
  const canDelete = state === 'draft' || state === 'rejected';

  // Only changed fields; a cleared one that had a value is removed (null, merge patch).
  const changes: OrderDetails = {};
  const change = (key: keyof OrderDetails, now: string, before: string | undefined) => {
    if (now !== (before ?? '')) changes[key] = now === '' ? null : now;
  };
  change('description', description.trim(), order.description);
  change('priority', priority, order.priority);
  change('category', category.trim(), order.category);

  /** Run a write: the server's refusal stays in the dialog with the entered values. */
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setProblem(null);
    setUnsure(false);
    try {
      await action();
    } catch (error) {
      setProblem(explain(error));
      setUnsure(uncertain(error));
    } finally {
      setBusy(false);
    }
  }
  const done = (message: string) => {
    toast(message);
    onChanged();
    onClose();
  };
  const open = (next: Panel) => {
    setPanel(panel === next ? null : next);
    setProblem(null);
    setUnsure(false);
  };

  const submit = () =>
    run(async () => {
      const result = await submitDraft(orderId);
      if (result.state === 'inProgress') {
        onWatch(result.id, `Order ${shortId(orderId)}`);
        done('Draft submitted: accepted, the billing worker completes it shortly');
      } else if (result.state === 'completed') {
        done('Draft submitted: the order is completed');
      } else {
        // rejected: keep the dialog, with the reasons the server gave
        setOrder({
          ...order,
          state: result.state as NonNullable<Order['state']>,
          productOrderErrorMessage: result.reasons.map((r) => ({
            '@type': 'ProductOrderErrorMessage',
            reason: r,
          })),
        });
        setPanel(null);
        onChanged();
      }
    });

  if (editing && draft !== null)
    return (
      <EditDraft
        order={order}
        draft={draft}
        onClose={() => setEditing(false)}
        onSaved={() => {
          onChanged();
          onClose();
        }}
      />
    );

  return (
    <Modal
      title={`Order ${shortId(orderId)}`}
      subtitle={<StatusPill kind="order" status={order.state ?? ''} label={order.state ?? '—'} />}
      width={680}
      ui="ui.orders.detail"
      onClose={onClose}
      footer={
        <>
          {problem !== null && (
            <div className="footer-push bss-inline-error" role="alert">
              {unsure ? `Unsure whether it was saved: ${problem}` : problem}{' '}
              {unsure && (
                <Button
                  small
                  onClick={() => {
                    onChanged();
                    onClose();
                  }}
                >
                  Refresh
                </Button>
              )}
            </div>
          )}
          {canDelete && (
            <Button data-ui="ui.orders.deleteDraft" disabled={busy} onClick={() => open('delete')}>
              Delete
            </Button>
          )}
          {canCancel && (
            <Button data-ui="ui.orders.cancel" disabled={busy} onClick={() => open('cancel')}>
              Cancel order
            </Button>
          )}
          <Button data-ui="ui.orders.editDetails" disabled={busy} onClick={() => open('details')}>
            Edit details
          </Button>
          {state === 'draft' && draft !== null && (
            <Button data-ui="ui.orders.editDraft" disabled={busy} onClick={() => setEditing(true)}>
              Edit draft
            </Button>
          )}
          {state === 'draft' && (
            <Button
              variant="primary"
              data-ui="ui.orders.submitDraft"
              disabled={busy}
              onClick={() => void submit()}
            >
              Submit
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <dl className="bss-details">
          <dt>Id</dt>
          <dd className="bss-mono">{orderId}</dd>
          <dt>Account</dt>
          <dd>
            {accountId !== undefined ? (
              <>
                <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId })}>
                  {accountLabel(accountId)}
                </Link>{' '}
                {order.billingAccount?.name}
              </>
            ) : (
              '—'
            )}
          </dd>
          <dt>Requested start</dt>
          <dd className="bss-mono">{day(order.requestedStartDate, context.timeZone)}</dd>
          <dt>Created</dt>
          <dd className="bss-mono">{when(order.creationDate)}</dd>
          <dt>Completed</dt>
          <dd className="bss-mono">{when(order.completionDate)}</dd>
          <dt>Cancelled</dt>
          <dd className="bss-mono">{when(order.cancellationDate)}</dd>
          {order.cancellationReason !== undefined && (
            <>
              <dt>Cancellation reason</dt>
              <dd>{order.cancellationReason}</dd>
            </>
          )}
        </dl>
        {state === 'draft' && draft === null && (
          <div className="bss-caption" data-ui="ui.orders.draftViaApi">
            Edit this draft through the API.
          </div>
        )}
        {panel === 'cancel' && (
          <div className="bss-form">
            <label className="bss-field">
              Cancellation reason (optional){' '}
              <TextInput
                aria-label="Cancellation reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <div>
              <Button
                variant="danger"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await cancelOrder(orderId, reason.trim() || undefined);
                    done('Order cancelled');
                  })
                }
              >
                Cancel this order
              </Button>
            </div>
          </div>
        )}
        {panel === 'details' && (
          <div className="bss-form">
            <label className="bss-field">
              Description{' '}
              <TextInput
                aria-label="Order description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label className="bss-field">
              Category{' '}
              <TextInput
                aria-label="Order category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
            </label>
            <label className="bss-field">
              Priority{' '}
              <select
                className="bss-input"
                aria-label="Order priority"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
              >
                <option value="">Not set</option>
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {p === '0' ? '0 (highest)' : p}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <Button
                variant="primary"
                disabled={busy || Object.keys(changes).length === 0}
                onClick={() =>
                  void run(async () => {
                    await patchOrderDetails(orderId, changes);
                    done('Order details saved');
                  })
                }
              >
                Save details
              </Button>
            </div>
          </div>
        )}
        {panel === 'delete' && (
          <div className="bss-run-note">
            Delete this {state} order for good? Nothing refers to it.{' '}
            <Button
              variant="danger"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await removeOrder(orderId);
                  done('Order deleted');
                })
              }
            >
              Delete order
            </Button>
          </div>
        )}
        {command.status === 'ok' && command.data !== null && (
          <div className="bss-run-note" data-ui="ui.orders.command" role="status">
            The billing worker is still finishing this order.{' '}
            <Link className="bss-link" href={`/prepaid-commands?account=${accountId}`}>
              Open its command
            </Link>
          </div>
        )}
        {(order.productOrderErrorMessage ?? []).length > 0 && (
          <div className="bss-run-error inline" data-ui="ui.orders.errors" role="alert">
            {(order.productOrderErrorMessage ?? []).map((m, i) => (
              <div key={i}>{m.reason ?? m.message ?? m.code}</div>
            ))}
          </div>
        )}
        <h3 className="bss-subhead">Items</h3>
        <table className="bss-mini" data-ui="ui.orders.items">
          <thead>
            <tr>
              <th>Action</th>
              <th>Offering</th>
              <th>Product</th>
              <th>State</th>
              <th>Price</th>
            </tr>
          </thead>
          <tbody>
            {(order.productOrderItem ?? []).map((item, i) => (
              <tr key={item.id ?? i}>
                <td>{item.action}</td>
                <td>{item.productOffering?.name ?? '—'}</td>
                <td className="bss-mono" title={item.product?.id}>
                  {item.product?.id !== undefined ? shortId(item.product.id) : '—'}
                </td>
                <td>{item.state ?? '—'}</td>
                <td className="bss-mono">{priceText(item)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3 className="bss-subhead">Cancellations</h3>
        {cancellations.status === 'loading' && <div className="bss-caption">Loading…</div>}
        {cancellations.status === 'error' && (
          <div className="bss-inline-error" role="alert">
            Failed to load the cancellations ({cancellations.error.message}){' '}
            <Button small onClick={cancellations.retry}>
              Retry
            </Button>
          </div>
        )}
        {cancellations.status === 'ok' &&
          (cancellations.data.length === 0 ? (
            <div className="bss-caption" data-ui="ui.orders.noCancellations">
              No cancellations.
            </div>
          ) : (
            <table className="bss-mini" data-ui="ui.orders.cancellations">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>State</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {cancellations.data.map((c) => (
                  <tr key={c.id}>
                    <td className="bss-mono">
                      {when(c.effectiveCancellationDate ?? c.creationDate)}
                    </td>
                    <td>{c.state ?? '—'}</td>
                    <td>{c.cancellationReason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </div>
    </Modal>
  );
}
