import { useState } from 'react';
import { loadOfferingDetail, loadPriceDetail, midnightOf, type Account } from '../api/bss';
import { loadOrderPage, type Order, type OrderItem, type OrderState } from '../api/orders';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate } from '../format/dates';
import { Id } from '../ui/Id';
import { Amount } from '../ui/Amount';
import { Button, Panel, StatusPill } from '../ui/kit';
import { ErrorBlock, ListState } from '../ui/states';
import { OrderModal } from './Orders';

async function loadOrders(account: string, signal: AbortSignal) {
  const groups = await Promise.all(
    (['draft', 'acknowledged', 'inProgress'] as OrderState[]).map(async (state) => {
      const items: Order[] = [];
      let offset = 0;
      for (;;) {
        const page = await loadOrderPage({ account, state }, offset, signal);
        items.push(...page.items);
        offset += page.items.length;
        if (!page.more || page.items.length === 0) return items;
      }
    }),
  );
  return groups.flat();
}

function DraftPrice({ account, order, item }: { account: Account; order: Order; item: OrderItem }) {
  const context = useSystemContext();
  const start = order.requestedStartDate
    ? businessDate(order.requestedStartDate, context.timeZone)
    : context.businessDate;
  const offering = item.productOffering?.id;
  const price = useResource(`draft-price:${account.id}:${offering}:${start}`, async (signal) => {
    if (!offering) return null;
    const detail = await loadOfferingDetail(offering, signal);
    const prices = await Promise.all(
      detail.priceRefs.map((ref) => loadPriceDetail(ref.id, signal)),
    );
    const at = Date.parse(midnightOf(start, context.timeZone));
    return (
      prices.find(
        (p) =>
          p.priceType === 'recurring' &&
          p.cents !== null &&
          p.currency === account.currency &&
          (p.validFrom === null || Date.parse(p.validFrom) <= at) &&
          (p.validTo === null || at < Date.parse(p.validTo)),
      ) ?? null
    );
  });
  if (price.status === 'loading') return <span>Loading price…</span>;
  if (price.status === 'error')
    return (
      <ErrorBlock title="Failed to load draft price" error={price.error} onRetry={price.retry} />
    );
  return price.data ? (
    <Amount cents={price.data.cents ?? 0} currency={account.currency} />
  ) : (
    <span>—</span>
  );
}

export function SubscriberOrders({
  account,
  version,
  onChanged,
  onWatch,
}: {
  account: Account;
  version: number;
  onChanged: () => void;
  onWatch: (id: string, label: string) => void;
}) {
  const [open, setOpen] = useState<Order | null>(null);
  const orders = useResource(`subscriber-orders:${account.id}:${version}`, (signal) =>
    loadOrders(account.id, signal),
  );
  return (
    <>
      <div className="subscriber-orders" data-ui="ui.subscriber.unfinishedOrders">
        <Panel title="Drafts and orders in progress">
          <ListState
            resource={orders}
            columns={[{ key: 'order', label: 'Order', width: '1fr' }]}
            errorTitle="Failed to load orders"
            empty={{
              title: 'No draft or in-progress orders.',
              text: 'Saved drafts and accepted orders appear here.',
            }}
            count={(data) => data.length}
            rows={(data) => data}
            onResetFilters={() => {}}
          >
            {(rows) => (
              <ul className="subscriber-orders-list">
                {rows.map((order) => (
                  <li key={order.id}>
                    <Id id={order.id ?? ''} prefix="ORD-" />
                    <StatusPill kind="order" status={order.state ?? ''} />
                    {order.productOrderItem?.map((item) => (
                      <span key={item.id}>
                        {' '}
                        {item.productOffering?.name ?? item.product?.name ?? 'Product'}{' '}
                        {order.state === 'draft' && (
                          <DraftPrice account={account} order={order} item={item} />
                        )}
                      </span>
                    ))}{' '}
                    <Button small data-ui="ui.subscriber.openOrder" onClick={() => setOpen(order)}>
                      Open
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </ListState>
        </Panel>
      </div>
      {open && (
        <OrderModal
          order={open}
          onClose={() => setOpen(null)}
          onChanged={onChanged}
          onWatch={onWatch}
        />
      )}
    </>
  );
}
