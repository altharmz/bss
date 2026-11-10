import { orderRefusal } from '../api/orderErrors';
import { Footer } from './FinancialForms';
import { submitDialogPrimary } from '../ui/kit';
import { statusPresentation } from '../ui/status';
import { Note } from '../ui/Note';
import { useState } from 'react';
import {
  cancelOrder,
  loadAccount,
  patchOrderDetails,
  removeOrder,
  submitDraft,
  type OrderDetails,
  type SimType,
} from '../api/bss';
import { explain, getJson } from '../api/client';
import { PATHS } from '../api/bss';
import {
  findOrderCommand,
  loadCancellations,
  loadNewestOrderPage,
  loadOrder,
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
import { formatInt, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import {
  Button,
  Dialog,
  Field,
  KeyValue,
  PageHeader,
  SelectField,
  StatusPill,
  Segmented,
  TextInput,
  useToast,
} from '../ui/kit';
import { AddProductModal, type DraftToEdit } from './AddProduct';
import { NetworkCommands } from './NetworkCommands';
import { OrderWatch } from './PrepaidOrderWatch';
import { watch, type Watched } from './watched';
import { ErrorBlock } from '../ui/states';
import { ListState } from '../ui/states';
import { uncertain } from './submissionKey';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// The global Orders list (docs/decisions/frontend.md, docs/ui.md):
// TMF622 reads, and (slice 10) the order dialog's writes: cancel, edit details, drafts.

const COLUMNS: Column[] = [
  { key: 'id', label: 'Order', width: '120px' },
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1fr)' },
  { key: 'state', label: 'State', width: '120px' },
  { key: 'items', label: 'Items', width: 'minmax(0, 1.4fr)' },
  { key: 'start', label: 'Start', width: '104px', mono: true },
  { key: 'created', label: 'Created', width: '104px', mono: true },
  { key: 'ended', label: 'Completed / cancelled', width: '184px', mono: true },
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

/** Prefer the order's snapshot over the current inventory name. */
export function itemText(item: OrderItem, names: Record<string, string> = {}): string {
  const product =
    item.product?.name ??
    names[item.product?.id ?? ''] ??
    (item.product?.id ? `product ${shortId(item.product.id)}` : 'product');
  const offering = item.productOffering?.name ?? 'offering';
  if (item.action === 'add') return `Add ${offering}`;
  if (item.action === 'delete') return `End ${product}`;
  if (item.action === 'modify') {
    if (targetStatus(item) === 'suspended') return `Suspend ${product}`;
    if (targetStatus(item) === 'active') return `Resume ${product}`;
    if (item.productOffering) return `Change ${product} to ${offering}`;
    if (
      item.product &&
      'realizingResource' in item.product &&
      item.product.realizingResource?.length
    )
      return `Change number of ${product}`;
  }
  return `Change ${product}`;
}

function targetStatus(item: OrderItem) {
  if (item.product && 'status' in item.product && item.product.status) return item.product.status;
  if (item.action === 'delete') return 'terminated';
  if (item.action === 'add') return 'active';
  return null;
}

function itemSummary(order: Order): string {
  return (order.productOrderItem ?? []).map((item) => itemText(item)).join(', ');
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
  const [changed, setChanged] = useState<Record<string, Order | null>>({});
  const [rowError, setRowError] = useState<{ id: string; error: Error } | null>(null);
  const list = useResource(
    `orders:${state}:${account ?? ''}:${offset}`,
    async (signal) => {
      const page = await loadNewestOrderPage(
        { state: state === 'all' ? null : state, account },
        offset,
        signal,
      );
      if (!signal.aborted) setChanged({});
      return { ...page, offset, state };
    },
    true,
  );

  const dropAccount = () => {
    window.history.replaceState(null, '', '/orders');
    setAccount(null);
    setOffset(0);
  };
  const refreshRow = async (id: string) => {
    try {
      const row = await loadOrder(id);
      setChanged((old) => ({ ...old, [id]: row }));
      setRowError(null);
    } catch (error) {
      setRowError({ id, error: error instanceof Error ? error : new Error(String(error)) });
    }
  };
  const name =
    list.status === 'ok'
      ? (list.data.items.find((o) => o.billingAccount?.id === account)?.billingAccount?.name ??
        null)
      : null;

  const visibleRows =
    list.status === 'ok'
      ? list.data.items.flatMap((row) => {
          const current = changed[row.id ?? ''] === undefined ? row : changed[row.id ?? ''];
          return current && (list.data.state === 'all' || current.state === list.data.state)
            ? [current]
            : [];
        })
      : [];
  const visibleList =
    list.status === 'ok'
      ? {
          ...list,
          data: {
            ...list.data,
            items: visibleRows,
          },
        }
      : list;
  return (
    <div className="bss-screen" data-ui="ui.orders">
      <PageHeader
        title="Orders"
        meta="Every product order, newest first"
        filters={
          <>
            <Segmented
              label="State"
              value={state}
              onChange={(next) => {
                setState(next);
                setOffset(0);
              }}
              options={(['all', ...ORDER_STATES] as const).map((key) => ({
                key,
                label: key === 'all' ? 'All' : statusPresentation('order', key)[1],
                title: key === 'all' ? undefined : key,
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
          </>
        }
      />
      {watched !== null && (
        <OrderWatch
          watched={watched}
          onDismiss={() => setWatched(null)}
          onSettled={(message) => {
            setWatched(null);
            toast(message);
            void refreshRow(watched.id);
            refreshNav();
          }}
        />
      )}
      <div role="table" aria-label="Orders" className="bss-page-table">
        {rowError && (
          <ErrorBlock
            title="Failed to refresh order"
            error={rowError.error}
            onRetry={() => void refreshRow(rowError.id)}
          />
        )}
        {list.refreshError && (
          <ErrorBlock
            title="Failed to refresh orders"
            error={list.refreshError}
            onRetry={list.retry}
          />
        )}
        <TableHead columns={COLUMNS} />
        <ListState
          resource={visibleList}
          columns={COLUMNS}
          errorTitle="Failed to load orders"
          empty={{
            title: 'No orders',
            text: 'Orders appear here once a product is added, changed or ended.',
          }}
          count={(data) => data.items.length}
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
                    id: order.id !== undefined ? <Id prefix="ORD-" id={order.id} /> : '—',
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
                    state: <StatusPill kind="order" status={order.state ?? ''} />,
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
                left={`Showing ${formatInt(data.offset + 1)}–${formatInt(data.offset + rows.length)}`}
                right={
                  <span className="bss-table-pager">
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
                      disabled={!data.more}
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
        {visibleList.status === 'ok' && visibleRows.length === 0 && offset > 0 && (
          <Button
            small
            data-ui="ui.orders.previous"
            onClick={() => setOffset(Math.max(0, offset - ORDER_PAGE))}
          >
            Previous
          </Button>
        )}
      </div>
      {open !== null && (
        <OrderModal
          order={open}
          onClose={() => setOpen(null)}
          onChanged={(updated) => {
            const id = open.id ?? '';
            if (updated === undefined) void refreshRow(id);
            else setChanged((old) => ({ ...old, [id]: updated }));
            refreshNav();
          }}
          onWatch={(id, label) => setWatched(watch('order', id, label))}
        />
      )}
    </div>
  );
}

function PriceCell({ item }: { item: OrderItem }) {
  const price = item.itemTotalPrice?.[0]?.price?.taxIncludedAmount;
  return price?.value === undefined ? (
    '—'
  ) : (
    <Amount cents={toCents(price.value)} {...(price.unit ? { currency: price.unit } : {})} />
  );
}

const ITEM_COLUMNS: Column[] = [
  { key: 'action', label: 'Action', width: 'minmax(0, 1.6fr)' },
  { key: 'offering', label: 'Offering', width: 'minmax(0, 1fr)' },
  { key: 'product', label: 'Product', width: '120px' },
  { key: 'state', label: 'State', width: '110px' },
  { key: 'target', label: 'Target status', width: '120px' },
  { key: 'price', label: 'Price', width: '110px', align: 'right' },
];
const CANCEL_COLUMNS: Column[] = [
  { key: 'date', label: 'Date', width: '170px', mono: true },
  { key: 'state', label: 'State', width: '140px' },
  { key: 'reason', label: 'Reason', width: 'minmax(0, 1fr)' },
];

type Panel = 'cancel' | 'details' | 'delete' | null;
const PRIORITIES = ['0', '1', '2', '3', '4'];

/** The draft's add item as Add product reopens it, or null for a draft of another shape
 * (those are edited through the API). */
function simTypeFields(product: unknown): { simType?: SimType } {
  const value = (
    product as { productCharacteristic?: { name?: string; value?: unknown }[] } | undefined
  )?.productCharacteristic?.find((c) => c.name === 'simType')?.value;
  return value === 'physical' || value === 'esim' ? { simType: value } : {};
}

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
  const resources = (item.product as { realizingResource?: { id?: string }[] } | undefined)
    ?.realizingResource;
  const resource = resources?.[0];
  return {
    orderId: order.id,
    itemId: item.id ?? '1',
    offeringId: item.productOffering.id,
    start:
      order.requestedStartDate === undefined
        ? null
        : businessDate(order.requestedStartDate, timeZone),
    numberId: resource?.id ?? '',
    imsiId: resources?.[1]?.id ?? '',
    ...simTypeFields(item.product),
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
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Edit draft"
      size="M"
      onClose={onClose}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      {account.status === 'error' ? (
        <ErrorBlock
          title="Failed to load the account"
          error={account.error}
          onRetry={account.retry}
        />
      ) : (
        <div className="bss-caption">Loading…</div>
      )}
    </Dialog>
  );
}

export function OrderModal({
  order: initial,
  onClose,
  onChanged,
  onWatch,
}: {
  order: Order;
  onClose: () => void;
  /** Update the changed row and the sidebar counts. Null removes a deleted row. */
  onChanged: (updated?: Order | null) => void;
  onWatch: (orderId: string, label: string) => void;
}) {
  const context = useSystemContext();
  const toast = useToast();
  const [order, setOrder] = useState(initial);
  const [panel, setPanel] = useState<Panel>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [code, setCode] = useState<string | undefined>();
  const [unsure, setUnsure] = useState(false);
  const [saved, setSaved] = useState(false);
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
  const names = useResource(`order-products:${orderId}`, async (signal) => {
    const ids = [
      ...new Set(
        (order.productOrderItem ?? []).flatMap((item) =>
          item.action !== 'add' && !item.product?.name && item.product?.id ? [item.product.id] : [],
        ),
      ),
    ];
    return Object.fromEntries(
      await Promise.all(
        ids.map(async (id) => {
          const product = await getJson<{ name?: string }>(`${PATHS.products}/${id}`, signal);
          return [id, product.name ?? `product ${shortId(id)}`];
        }),
      ),
    );
  });
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
    setSaved(false);
    setBusy(true);
    setProblem(null);
    setCode(undefined);
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
  const done = async (message: string) => {
    await onChanged(message === 'Order deleted' ? null : await loadOrder(orderId));
    toast(message);
    onClose();
  };
  const open = (next: Panel) => {
    setPanel(panel === next ? null : next);
    setProblem(null);
    setCode(undefined);
    setUnsure(false);
  };

  const submit = () =>
    run(async () => {
      const result = await submitDraft(orderId);
      if (result.state === 'inProgress') {
        onWatch(result.id, `Order ${shortId(orderId)}`);
        await done('Draft submitted: accepted, the billing worker completes it shortly');
      } else if (result.state === 'completed') {
        await done('Draft submitted: the order is completed');
      } else {
        // rejected: keep the dialog, with the reasons the server gave
        setOrder({
          ...order,
          state: result.state as NonNullable<Order['state']>,
          productOrderErrorMessage: result.errors.map((error) => ({
            '@type': 'ProductOrderErrorMessage',
            ...error,
          })),
        });
        setProblem(result.refusal.problem);
        setCode(result.refusal.code);
        setPanel(null);
        await onChanged();
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
    <Dialog
      onSubmit={panel === null || panel === 'details' ? submitDialogPrimary : undefined}
      title={`Order ${shortId(orderId)}`}
      context={
        <>
          {orderId !== '' && <Id prefix="ORD-" id={orderId} />}{' '}
          <StatusPill kind="order" status={order.state ?? ''} />
        </>
      }
      size="L"
      ui="ui.orders.detail"
      onClose={onClose}
      footer={
        <Footer
          write={{ busy, problem, unsure, code }}
          left={
            saved && Object.keys(changes).length === 0 ? (
              <span role="status" data-ui="ui.orders.detailsSaved">
                Details saved
              </span>
            ) : undefined
          }
          onRefresh={() => {
            onChanged();
            onClose();
          }}
        >
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
          {panel === 'details' && (
            <Button
              variant="primary"
              disabled={busy || Object.keys(changes).length === 0}
              onClick={() =>
                void run(async () => {
                  await patchOrderDetails(orderId, changes);
                  const updated = await loadOrder(orderId);
                  setOrder(updated);
                  setDescription(updated.description ?? '');
                  setCategory(updated.category ?? '');
                  setPriority(updated.priority ?? '');
                  await onChanged(updated);
                  setSaved(true);
                })
              }
            >
              Save details
            </Button>
          )}
          {state === 'draft' && (
            <Button
              variant={panel === 'details' ? 'secondary' : 'primary'}
              data-ui="ui.orders.submitDraft"
              disabled={busy}
              onClick={() => void submit()}
            >
              Submit
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </Footer>
      }
    >
      <>
        <KeyValue
          items={[
            [
              'Id',
              <span key="i" className="bss-mono">
                {orderId}
              </span>,
            ],
            [
              'Account',
              accountId !== undefined ? (
                <>
                  <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId })}>
                    {accountLabel(accountId)}
                  </Link>{' '}
                  {order.billingAccount?.name}
                </>
              ) : (
                '—'
              ),
            ],
            ['Requested start', day(order.requestedStartDate, context.timeZone)],
            ['Created', when(order.creationDate)],
            ['Completed', when(order.completionDate)],
            ['Cancelled', when(order.cancellationDate)],
            ...(order.cancellationReason !== undefined
              ? [['Cancellation reason', order.cancellationReason] as [string, string]]
              : []),
          ]}
        />
        {state === 'draft' && draft === null && (
          <div className="bss-caption" data-ui="ui.orders.draftViaApi">
            Edit this draft through the API.
          </div>
        )}
        {panel === 'cancel' && (
          <div className="bss-form">
            <Field label="Cancellation reason (optional)">
              <TextInput
                aria-label="Cancellation reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </Field>
            <div>
              <Button
                variant="danger"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await cancelOrder(orderId, reason.trim() || undefined);
                    await done('Order cancelled');
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
            <Field label="Description">
              <TextInput
                aria-label="Order description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </Field>
            <Field label="Category">
              <TextInput
                aria-label="Order category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
            </Field>
            <Field label="Priority">
              <SelectField
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
              </SelectField>
            </Field>
          </div>
        )}
        {panel === 'delete' && (
          <Note tone="warning">
            Delete this {state} order for good? Nothing refers to it.{' '}
            <Button
              variant="danger"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await removeOrder(orderId);
                  await done('Order deleted');
                })
              }
            >
              Delete order
            </Button>
          </Note>
        )}
        {command.status === 'ok' && command.data !== null && (
          <Note tone="info" data-ui="ui.orders.command">
            The billing worker is still finishing this order.{' '}
            <Link className="bss-link" href={`/prepaid-commands?account=${accountId}`}>
              Open its command
            </Link>
          </Note>
        )}
        {problem === null && (order.productOrderErrorMessage ?? []).length > 0 && (
          <Note tone="negative" data-ui="ui.orders.errors">
            {(order.productOrderErrorMessage ?? []).map((m, i) => (
              <div key={i}>{orderRefusal([m]).problem}</div>
            ))}
          </Note>
        )}
        {names.status === 'error' && (
          <ErrorBlock
            title="Failed to load product names"
            error={names.error}
            onRetry={names.retry}
          />
        )}
        <h3 className="bss-subhead">Items</h3>
        <div
          role="table"
          aria-label="Order items"
          className="bss-dialog-table"
          data-ui="ui.orders.items"
        >
          <TableHead columns={ITEM_COLUMNS} density="compact" />
          {(order.productOrderItem ?? []).map((item, i) => (
            <TableRow
              key={item.id ?? i}
              columns={ITEM_COLUMNS}
              density="compact"
              cells={{
                action: itemText(item, names.status === 'ok' ? names.data : {}),
                offering: item.productOffering?.name ?? '—',
                product:
                  item.product?.id !== undefined ? <Id prefix="PRD-" id={item.product.id} /> : '—',
                state: item.state ? <StatusPill kind="order" status={item.state} /> : '—',
                target: targetStatus(item) ? (
                  <StatusPill kind="product" status={targetStatus(item) ?? ''} />
                ) : (
                  '—'
                ),
                price: <PriceCell item={item} />,
              }}
            />
          ))}
        </div>
        {orderId !== '' && <NetworkCommands owner={{ orderId }} />}
        <h3 className="bss-subhead">Cancellations</h3>
        {cancellations.status === 'loading' && <div className="bss-caption">Loading…</div>}
        {cancellations.status === 'error' && (
          <Note tone="negative">
            Failed to load the cancellations ({cancellations.error.message}){' '}
            <Button small onClick={cancellations.retry}>
              Retry
            </Button>
          </Note>
        )}
        {cancellations.status === 'ok' &&
          (cancellations.data.length === 0 ? (
            <div className="bss-caption" data-ui="ui.orders.noCancellations">
              No cancellations.
            </div>
          ) : (
            <div
              role="table"
              aria-label="Cancellations"
              className="bss-dialog-table"
              data-ui="ui.orders.cancellations"
            >
              <TableHead columns={CANCEL_COLUMNS} density="compact" />
              {cancellations.data.map((c) => (
                <TableRow
                  key={c.id}
                  columns={CANCEL_COLUMNS}
                  density="compact"
                  cells={{
                    date: when(c.effectiveCancellationDate ?? c.creationDate),
                    state: c.state ? <StatusPill kind="order" status={c.state} /> : '—',
                    reason: c.cancellationReason ?? '—',
                  }}
                />
              ))}
            </div>
          ))}
      </>
    </Dialog>
  );
}
