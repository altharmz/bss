import { submitDialogPrimary } from '../ui/kit';
import { useState } from 'react';
import { loadNumbers } from '../api/numbers';
import { loadProduct, loadProducts, PRODUCT_STATES, type InventoryProduct } from '../api/products';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { Button, Dialog, PageHeader, Panel, StatusPill, TextInput } from '../ui/kit';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// The global Products list (docs/decisions/frontend.md, slice 21): TMF637 reads only.
// Products are added and ended by orders alone, so nothing here writes.

const COLUMNS: Column[] = [
  { key: 'id', label: 'Product', width: '104px', mono: true },
  { key: 'offering', label: 'Offering', width: 'minmax(0, 1.2fr)' },
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1fr)' },
  { key: 'status', label: 'Status', width: '140px' },
  { key: 'start', label: 'Start', width: '104px', mono: true },
  { key: 'end', label: 'End', width: '104px', mono: true },
  { key: 'number', label: 'Number', width: '140px', mono: true },
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
      <div className="bss-title-row">
        <PageHeader title="Products" />
        <span className="bss-caption">Every product, in every status</span>
      </div>
      <div className="bss-toolbar">
        <select
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
        </select>
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
      </div>
      <Panel>
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
                  id: <span title={p.id}>{shortId(p.id ?? '')}</span>,
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
                  status: (
                    <StatusPill kind="product" status={p.status ?? ''} label={p.status ?? '—'} />
                  ),
                  start: day(p.startDate, context.timeZone),
                  end: day(p.terminationDate, context.timeZone),
                  number: digits(p) || '—',
                }}
              />
            ))
          }
        </ListState>
      </Panel>
      {open !== null && <ProductDetail product={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ProductDetail({ product, onClose }: { product: InventoryProduct; onClose: () => void }) {
  const context = useSystemContext();
  const id = product.id ?? '';
  const loaded = useResource(`product:${id}`, (signal) => loadProduct(id, signal));
  const p = loaded.status === 'ok' ? loaded.data : product;
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={`Product ${shortId(id)}`}
      context={p.productOffering?.name ?? p.name}
      size="L"
      onClose={onClose}
      ui="ui.products.detail"
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="bss-modal-body">
        {loaded.status === 'error' && (
          <ErrorBlock
            title="Failed to load the product"
            error={loaded.error}
            onRetry={loaded.retry}
          />
        )}
        {loaded.status === 'loading' && <p>Loading…</p>}
        <p>
          <StatusPill kind="product" status={p.status ?? ''} label={p.status ?? '—'} /> · starts{' '}
          {day(p.startDate, context.timeZone)} · ends {day(p.terminationDate, context.timeZone)}
        </p>
        <p>
          Ordered {day(p.orderDate, context.timeZone)} · created{' '}
          {day(p.creationDate, context.timeZone)}
        </p>
        {p.productSpecification?.name !== undefined && (
          <p>Specification: {p.productSpecification.name}</p>
        )}
        {p.billingAccount?.id !== undefined && (
          <p>
            Subscriber:{' '}
            <Link href={hrefOf({ name: 'subscriber', accountId: p.billingAccount.id })}>
              {p.billingAccount.name ?? accountLabel(p.billingAccount.id)}
            </Link>
          </p>
        )}
        <div data-ui="ui.products.orderItems">
          <strong>Order items</strong>
          {(p.productOrderItem ?? []).length === 0 && <p>None.</p>}
          {(p.productOrderItem ?? []).map((item) => (
            <p key={`${item.orderId}:${item.orderItemId}`}>
              {item.role} · {item.orderItemAction} · order{' '}
              {item.orderId !== undefined ? shortId(item.orderId) : '—'}
              {p.billingAccount?.id !== undefined && (
                <>
                  {' '}
                  · <Link href={`/orders?account=${p.billingAccount.id}`}>Open in Orders</Link>
                </>
              )}
            </p>
          ))}
        </div>
        <div data-ui="ui.products.resource">
          <strong>Realizing resource</strong>
          {(p.realizingResource ?? []).length === 0 && <p>None.</p>}
          {(p.realizingResource ?? []).map((r) => (
            <p key={r.id}>
              {r['@referredType'] ?? 'Resource'} {r.id !== undefined ? shortId(r.id) : ''} ·{' '}
              <Link href="/numbers">Open Numbers</Link>
            </p>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
