import { submitDialogPrimary } from '../ui/kit';
import { NetworkCommands } from './NetworkCommands';
import { useState } from 'react';
import { loadNumbers } from '../api/numbers';
import { loadProduct, loadProducts, PRODUCT_STATES, type InventoryProduct } from '../api/products';
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
  const [offering, setOffering] = useState('');
  const [number, setNumber] = useState('');
  const [open, setOpen] = useState<InventoryProduct | null>(null);
  const list = useResource(`products:${status}:${account ?? ''}`, (signal) =>
    loadProducts({ status: status === '' ? null : status, account }, signal),
  );
  // the product carries the number's id only; its digits come from the inventory
  const numbers = useResource('product-numbers', loadNumbers);
  const digits = (p: InventoryProduct): string => {
    const id = p.realizingResource?.[0]?.id;
    if (id === undefined) return '';
    if (numbers.status !== 'ok') return '…';
    return numbers.data.find((n) => n.id === id)?.value ?? shortId(id);
  };

  const dropAccount = () => {
    window.history.replaceState(null, '', '/products');
    setAccount(null);
  };
  const name =
    list.status === 'ok'
      ? (list.data.find((p) => p.billingAccount?.id === account)?.billingAccount?.name ?? null)
      : null;
  const reset = () => {
    setStatus('');
    setOffering('');
    setNumber('');
    if (account !== null) dropAccount();
  };

  return (
    <div className="bss-screen" data-ui="ui.products">
      <PageHeader
        title="Products"
        count={list.status === 'ok' ? list.data.length : undefined}
        meta="Every product, in every status"
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
              aria-label="Filter by offering"
              placeholder="Offering"
              value={offering}
              onChange={(e) => setOffering(e.target.value)}
            />
            <TextInput
              aria-label="Filter by number"
              placeholder="Number"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
            />
            {account !== null && (
              <span className="bss-run-tag" data-ui="ui.products.accountChip">
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
          total={(data) => data.length}
          rows={(data) =>
            data.filter(
              (p) =>
                (p.productOffering?.name ?? '')
                  .toLowerCase()
                  .includes(offering.trim().toLowerCase()) &&
                (number.trim() === '' || digits(p).includes(number.trim())),
            )
          }
          onResetFilters={reset}
        >
          {(rows) =>
            rows.map((p) => (
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
            ))
          }
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
                      <Link href={`/orders?account=${p.billingAccount.id}`}>Open in Orders</Link>
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
