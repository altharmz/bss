import { IconButton } from '../ui/kit';
import { SubscriberSearch } from './SubscriberSearch';
import { useState } from 'react';
import {
  loadNumberValues,
  loadProductPage,
  PRODUCT_PAGE,
  PRODUCT_STATES,
  type InventoryProduct,
} from '../api/products';
import { PageFooter, usePageOffset, useSettled } from '../ui/paging';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link, useRouter } from '../app/router';
import { businessDate } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { Id } from '../ui/Id';
import { PageHeader, SelectField, StatusPill, TextInput } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// The global Products list (docs/decisions/frontend.md, slice 21): TMF637 reads only.
// Products are added and ended by orders alone, so nothing here writes. A row opens the
// product's page (#458).

const COLUMNS: Column[] = [
  { key: 'id', label: 'Product', width: '120px' },
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
  // the name of the subscriber chosen in the search, for the chip before rows load
  const [chosenName, setChosenName] = useState<string | null>(null);
  const [number, setNumber] = useState('');
  const settledNumber = useSettled(number).replace(/\D/g, '');
  const { navigate } = useRouter();
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
                  onClick={() => {
                    if (p.id !== undefined) navigate(hrefOf({ name: 'product', productId: p.id }));
                  }}
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
    </div>
  );
}
