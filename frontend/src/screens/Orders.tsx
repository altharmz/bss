import { useState } from 'react';
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
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate, clockTime } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { formatAmount, formatInt } from '../format/money';
import { Button, Modal, PageTitle, Panel, Pill, Segmented } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// The global Orders list (docs/decisions/frontend.md, docs/next-slices-design.md section 9):
// TMF622 reads only. Writes (cancel, drafts) come with slice 10.

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
                    state: <Pill status={order.state ?? ''} label={order.state ?? '—'} />,
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
      {open !== null && <OrderModal order={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function priceText(item: OrderItem): string {
  const price = item.itemTotalPrice?.[0]?.price?.taxIncludedAmount;
  return price?.value === undefined
    ? '—'
    : `${formatAmount(price.value)} ${price.unit ?? ''}`.trim();
}

function OrderModal({ order, onClose }: { order: Order; onClose: () => void }) {
  const context = useSystemContext();
  const orderId = order.id ?? '';
  const accountId = order.billingAccount?.id;
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
  return (
    <Modal
      title={`Order ${shortId(orderId)}`}
      subtitle={<Pill status={order.state ?? ''} label={order.state ?? '—'} />}
      width={680}
      ui="ui.orders.detail"
      onClose={onClose}
      footer={<Button onClick={onClose}>Close</Button>}
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
