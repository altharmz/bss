import { submitDialogPrimary, IconButton } from '../ui/kit';
import { NetworkCommands } from './NetworkCommands';
import { SubscriberSearch } from './SubscriberSearch';
import { characteristicOf } from '../api/devices';
import { Note } from '../ui/Note';
import { useState } from 'react';
import {
  loadNumberValues,
  loadProduct,
  loadProductPage,
  PRODUCT_PAGE,
  PRODUCT_STATES,
  type InventoryProduct,
} from '../api/products';
import { PageFooter, usePageOffset, useSettled } from '../ui/paging';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { Id } from '../ui/Id';
import {
  Button,
  Dialog,
  KeyValue,
  PageHeader,
  SelectField,
  StatusPill,
  TextInput,
} from '../ui/kit';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// The global Products list (docs/decisions/frontend.md, slice 21): TMF637 reads only.
// Products are added and ended by orders alone, so nothing here writes.

const COLUMNS: Column[] = [
  { key: 'id', label: 'Product', width: '120px' },
  { key: 'offering', label: 'Offering', width: 'minmax(0, 1.2fr)' },
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1fr)' },
  { key: 'status', label: 'Status', width: '140px' },
  { key: 'start', label: 'Start', width: '104px', mono: true },
  { key: 'end', label: 'End', width: '104px', mono: true },
  { key: 'number', label: 'Number', width: '140px', mono: true },
];

const ITEM_COLUMNS: Column[] = [
  { key: 'item', label: 'Item', width: 'minmax(0, 1fr)' },
  { key: 'order', label: 'Order', width: '120px' },
  { key: 'open', label: '', width: '120px' },
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The account the subscriber card's link asks for (?account=<id>). */
function accountFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get('account');
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

function day(instant: string | undefined, timeZone: string): string {
  return instant === undefined ? '—' : businessDate(instant, timeZone);
}

export function ProductsScreen() {
  useSetCrumbs([{ label: 'Products' }], 'products');
  const context = useSystemContext();
  const [status, setStatus] = useState('');
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  // the name of the subscriber chosen in the search, for the chip before rows load
  const [chosenName, setChosenName] = useState<string | null>(null);
  const [number, setNumber] = useState('');
  const settledNumber = useSettled(number).replace(/\D/g, '');
  const [open, setOpen] = useState<InventoryProduct | null>(null);
  const filterKey = `${status}:${account ?? ''}:${settledNumber}`;
  const [offset, setOffset] = usePageOffset(filterKey);
  const list = useResource(`products:${filterKey}:${offset}`, (signal) =>
    loadProductPage(
      { status: status === '' ? null : status, account, number: settledNumber || null },
      offset,
      signal,
    ),
  );
  // the product carries the number's id only; its digits come from the inventory, read
  // for this page's products only
  const pageProducts = list.status === 'ok' ? list.data.items : [];
  const numbers = useResource(
    `product-numbers:${pageProducts.map((p) => p.id).join(',')}`,
    (signal) => loadNumberValues(pageProducts, signal),
  );
  const digits = (p: InventoryProduct): string => {
    const id = p.realizingResource?.[0]?.id;
    if (id === undefined) return '';
    if (numbers.status !== 'ok') return '…';
    return numbers.data[id] || shortId(id);
  };

  const chooseAccount = (chosen: { id: string; name: string }) => {
    window.history.replaceState(null, '', `/products?account=${chosen.id}`);
    setAccount(chosen.id);
    setChosenName(chosen.name);
  };
  const dropAccount = () => {
    window.history.replaceState(null, '', '/products');
    setAccount(null);
    setChosenName(null);
  };
  const name =
    list.status === 'ok'
      ? (list.data.items.find((p) => p.billingAccount?.id === account)?.billingAccount?.name ??
        chosenName)
      : chosenName;
  const filtered = status !== '' || account !== null || number !== '';
  const reset = () => {
    setStatus('');
    setNumber('');
    if (account !== null) dropAccount();
  };

  return (
    <div className="bss-screen" data-ui="ui.products">
      <PageHeader
        title="Products"
        meta="Products across all billing accounts. A billing account's products are on its card."
        filters={
          <>
            <SelectField
              aria-label="Status"
              data-ui="ui.products.status"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">All statuses</option>
              {PRODUCT_STATES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </SelectField>
            <TextInput
              aria-label="Filter by number"
              placeholder="Number (exact)"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
            />
            <SubscriberSearch ui="ui.products.subscriberSearch" onChoose={chooseAccount} />
            {account !== null && (
              <span className="bss-run-tag" data-ui="ui.products.accountChip">
                Account {name ?? accountLabel(account)}{' '}
                <IconButton
                  icon="x"
                  small
                  label="Remove the account filter"
                  onClick={dropAccount}
                />
              </span>
            )}
          </>
        }
      />
      <div role="table" aria-label="Products" className="bss-page-table">
        <TableHead columns={COLUMNS} />
        <ListState
          resource={list}
          columns={COLUMNS}
          errorTitle="Failed to load products"
          empty={{
            title: 'No products',
            text: 'Products appear here once an order adds one.',
          }}
          // filtered on the server: an empty filtered page is "no matches", not "no products"
          count={(data) => (filtered ? 1 : data.items.length)}
          rows={(data) => data.items}
          onResetFilters={reset}
        >
          {(rows, data) => (
            <>
              {rows.map((p) => (
                <TableRow
                  key={p.id}
                  columns={COLUMNS}
                  onClick={() => setOpen(p)}
                  cells={{
                    id: p.id !== undefined ? <Id prefix="PRD-" id={p.id} /> : '—',
                    offering: p.productOffering?.name ?? p.name ?? '—',
                    account:
                      p.billingAccount?.id !== undefined ? (
                        <span className="ellipsis" onClick={(event) => event.stopPropagation()}>
                          <Link
                            className="bss-link"
                            href={hrefOf({ name: 'subscriber', accountId: p.billingAccount.id })}
                          >
                            {p.billingAccount.name ?? accountLabel(p.billingAccount.id)}
                          </Link>
                        </span>
                      ) : (
                        '—'
                      ),
                    status: <StatusPill kind="product" status={p.status ?? ''} />,
                    start: day(p.startDate, context.timeZone),
                    end: day(p.terminationDate, context.timeZone),
                    number: digits(p) || '—',
                  }}
                />
              ))}
              <PageFooter
                offset={data.offset}
                shown={rows.length}
                more={data.more}
                size={PRODUCT_PAGE}
                onOffset={setOffset}
                ui="ui.products"
              />
            </>
          )}
        </ListState>
      </div>
      {open !== null && <ProductDetail product={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ProductDetail({ product, onClose }: { product: InventoryProduct; onClose: () => void }) {
  const context = useSystemContext();
  const id = product.id ?? '';
  const loaded = useResource(`product:${id}`, (signal) => loadProduct(id, signal));
  const p = loaded.status === 'ok' ? loaded.data : product;
  const orders = p.productOrderItem ?? [];
  const resources = p.realizingResource ?? [];
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={`Product ${shortId(id)}`}
      context={
        <>
          {id !== '' && <Id prefix="PRD-" id={id} />} {p.productOffering?.name ?? p.name}
        </>
      }
      size="M"
      onClose={onClose}
      ui="ui.products.detail"
      footer={<Button onClick={onClose}>Close</Button>}
    >
      {loaded.status === 'error' && (
        <ErrorBlock
          title="Failed to load the product"
          error={loaded.error}
          onRetry={loaded.retry}
        />
      )}
      {loaded.status === 'loading' && <p>Loading…</p>}
      <KeyValue
        items={[
          ['Status', <StatusPill key="s" kind="product" status={p.status ?? ''} />],
          ['Starts', day(p.startDate, context.timeZone)],
          ['Ends', day(p.terminationDate, context.timeZone)],
          ['Ordered', day(p.orderDate, context.timeZone)],
          ['Created', day(p.creationDate, context.timeZone)],
          ...(p.productSpecification?.name !== undefined
            ? [['Specification', p.productSpecification.name] as [string, string]]
            : []),
          [
            'Subscriber',
            p.billingAccount?.id !== undefined ? (
              <Link href={hrefOf({ name: 'subscriber', accountId: p.billingAccount.id })}>
                {p.billingAccount.name ?? accountLabel(p.billingAccount.id)}
              </Link>
            ) : (
              '—'
            ),
          ],
        ]}
      />
      <section className="bss-dialog-section" data-ui="ui.products.orderItems">
        <h3>Order items</h3>
        {orders.length === 0 ? (
          <p>None.</p>
        ) : (
          <div role="table" aria-label="Order items">
            <TableHead columns={ITEM_COLUMNS} density="compact" />
            {orders.map((item) => (
              <TableRow
                key={`${item.orderId}:${item.orderItemId}`}
                columns={ITEM_COLUMNS}
                density="compact"
                cells={{
                  item: `${item.role} · ${item.orderItemAction}`,
                  order: item.orderId !== undefined ? <Id prefix="ORD-" id={item.orderId} /> : '—',
                  open:
                    p.billingAccount?.id !== undefined ? (
                      <Link href={`/orders?account=${p.billingAccount.id}&state=all`}>
                        Open in Orders
                      </Link>
                    ) : (
                      ''
                    ),
                }}
              />
            ))}
          </div>
        )}
      </section>
      {id !== '' && <NetworkCommands owner={{ productId: id }} />}
      {characteristicOf(p, 'imei') !== undefined && (
        <section className="bss-dialog-section" data-ui="ui.products.device">
          <h3>Device</h3>
          <p>
            IMEI <span className="bss-mono">{characteristicOf(p, 'imei')}</span> ·{' '}
            {characteristicOf(p, 'deviceCompatibility') === 'compatible'
              ? 'works on the network'
              : 'not confirmed by the network'}
          </p>
          {characteristicOf(p, 'deviceReasons') !== undefined && (
            <Note tone="warning">{characteristicOf(p, 'deviceReasons')}</Note>
          )}
        </section>
      )}
      <section className="bss-dialog-section" data-ui="ui.products.resource">
        <h3>Realizing resource</h3>
        {resources.length === 0 && <p>None.</p>}
        {resources.map((r) => (
          <p key={r.id}>
            {r['@referredType'] ?? 'Resource'} {r.id !== undefined ? shortId(r.id) : ''} ·{' '}
            <Link href="/numbers">Open Numbers</Link>
          </p>
        ))}
      </section>
    </Dialog>
  );
}
