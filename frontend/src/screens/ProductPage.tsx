import { useState, type ReactNode } from 'react';
import {
  loadAccount,
  loadProducts,
  undoPendingChange,
  type Account,
  type AccountProduct,
} from '../api/bss';
import { loadProductRates, type RateRow } from '../api/bills';
import { explain } from '../api/client';
import { characteristicOf } from '../api/devices';
import { e911Text } from '../api/e911';
import { iccidOf, kindOf, loadRealizingNumbers, numberHref } from '../api/numbers';
import {
  HISTORY_PAGE,
  loadProduct,
  loadProductHistory,
  type InventoryProduct,
  type ProductHistoryEntry,
} from '../api/products';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { formatAmount, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import { Note } from '../ui/Note';
import { KeyValue, Menu, PageHeader, Panel, StatusPill, TabPanel, Tabs, useToast } from '../ui/kit';
import { PageFooter } from '../ui/paging';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { BillDialog } from './Bills';
import { E911Dialog } from './E911Address';
import { NetworkCommands } from './NetworkCommands';
import { PlanFacts, RenewalHistory } from './PrepaidPlans';
import {
  ChangeOfferingDialog,
  offeredChanges,
  ProductActionsMenu,
  ProductChangeDialog,
  productMenuExtra,
  type ProductAction,
} from './ProductActions';
import { DeviceDialog } from './ProductDevice';
import { NumberModal } from './ProductNumber';
import { TransferPinDialog } from './TransferPin';
import { ProductUsage } from './UsageTab';
import './screens.css';

// One product's page (#458, docs/decisions/frontend.md "Product page"): what the line is,
// what it has cost and what has happened to it. TMF637 gives the product and its order
// items; the billing account card's product view gives the price, the line's identities
// and the actions, which open the card's own dialogs with their requests. Money stays on
// the billing account: the page shows charges and usage, never a balance.

type Tab = 'overview' | 'charges' | 'usage' | 'history';
const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'charges', label: 'Charges' },
  { key: 'usage', label: 'Usage' },
  { key: 'history', label: 'History' },
];

const CHARGE_COLUMNS: Column[] = [
  { key: 'date', label: 'Date', width: '108px', mono: true },
  { key: 'charge', label: 'Charge', width: 'minmax(0, 1fr)' },
  { key: 'period', label: 'Period', width: '200px', mono: true },
  { key: 'amount', label: 'Amount', width: '110px', align: 'right', mono: true },
  { key: 'total', label: 'With tax', width: '110px', align: 'right', mono: true },
  { key: 'bill', label: 'Bill', width: '120px' },
];
const CHARGE_PAGE = 25;

const ITEM_COLUMNS: Column[] = [
  { key: 'item', label: 'Item', width: 'minmax(0, 1fr)' },
  { key: 'order', label: 'Order', width: '120px' },
  { key: 'open', label: '', width: '120px' },
];

const HISTORY_COLUMNS: Column[] = [
  { key: 'date', label: 'Date', width: '108px', mono: true },
  { key: 'change', label: 'Change', width: 'minmax(0, 1fr)' },
  { key: 'order', label: 'Order', width: '120px' },
];

const CAUSE: Record<string, string> = {
  manual: 'by an order',
  lifecycle: 'by the prepaid lifecycle',
  dunning: 'by dunning',
};

function tabFromHash(): Tab {
  const hash = window.location.hash.slice(1);
  return TABS.some((t) => t.key === hash) ? (hash as Tab) : 'overview';
}

function day(instant: string | undefined, timeZone: string): string {
  return instant === undefined ? '—' : businessDate(instant, timeZone);
}

function ordersHref(product: InventoryProduct): string | null {
  const account = product.billingAccount?.id;
  return account === undefined ? null : `/orders?account=${account}&state=all`;
}

export function ProductPageScreen({ productId }: { productId: string }) {
  const context = useSystemContext();
  const toast = useToast();
  const [version, setVersion] = useState(0);
  const reload = () => setVersion((v) => v + 1);
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const product = useResource(
    `product-page:${productId}:${version}`,
    (signal) => loadProduct(productId, signal),
    true,
  );
  const accountId = product.status === 'ok' ? product.data.billingAccount?.id : undefined;
  const account = useResource<Account | null>(
    `product-account:${accountId ?? ''}:${version}`,
    (signal) => (accountId ? loadAccount(accountId, signal) : Promise.resolve(null)),
    true,
  );
  // the card's view of the account's open products: this one's price, identities and
  // pending changes, and its add-ons or plan; an ended product is not among them
  const lines = useResource<AccountProduct[]>(
    `product-lines:${accountId ?? ''}:${version}`,
    (signal) => (accountId ? loadProducts(accountId, signal) : Promise.resolve([])),
    true,
  );
  const p = product.status === 'ok' ? product.data : null;
  const name = p?.productOffering?.name ?? p?.name ?? `Product ${shortId(productId)}`;
  useSetCrumbs(
    [{ label: 'Products', href: hrefOf({ name: 'products' }) }, { label: name }],
    `product:${productId}:${name}`,
  );
  const siblings = lines.status === 'ok' ? lines.data : [];
  const line = siblings.find((l) => l.id === productId) ?? null;
  const acc = account.status === 'ok' ? account.data : null;

  const choose = (key: Tab) => {
    setTab(key);
    window.history.replaceState(null, '', `${window.location.pathname}#${key}`);
  };
  const done = (message: string) => {
    toast(message);
    reload();
  };

  if (product.status === 'error') {
    return (
      <div className="bss-screen" data-ui="ui.product">
        <PageHeader title={`Product ${shortId(productId)}`} />
        <ErrorBlock
          title="Failed to load the product"
          error={product.error}
          onRetry={product.retry}
        />
      </div>
    );
  }
  if (p === null) {
    return (
      <div className="bss-screen" data-ui="ui.product">
        <PageHeader title="Product" />
        <SkeletonRows columns={HISTORY_COLUMNS} rows={3} />
      </div>
    );
  }
  return (
    <div className="bss-screen" data-ui="ui.product">
      <PageHeader
        title={name}
        meta={<HeaderFacts product={p} line={line} />}
        actions={
          line !== null && acc !== null ? (
            <Actions account={acc} line={line} onDone={done} onRefresh={reload} />
          ) : undefined
        }
      />
      <Tabs id="product" label="Product sections" options={TABS} value={tab} onChange={choose} />
      <div hidden={tab !== 'overview'}>
        <TabPanel id="product" value="overview">
          <Overview product={p} line={line} lines={siblings} account={acc} />
        </TabPanel>
      </div>
      <div hidden={tab !== 'charges'}>
        <TabPanel id="product" value="charges">
          {/* mounted only while shown: no request until the tab is opened */}
          {tab === 'charges' && (
            <ProductCharges productId={productId} timeZone={context.timeZone} onChanged={done} />
          )}
        </TabPanel>
      </div>
      <div hidden={tab !== 'usage'}>
        <TabPanel id="product" value="usage">
          {tab === 'usage' && <ProductUsage productId={productId} />}
        </TabPanel>
      </div>
      <div hidden={tab !== 'history'}>
        <TabPanel id="product" value="history">
          {tab === 'history' && <ProductHistory product={p} productId={productId} />}
        </TabPanel>
      </div>
    </div>
  );
}

function HeaderFacts({
  product,
  line,
}: {
  product: InventoryProduct;
  line: AccountProduct | null;
}) {
  const { timeZone } = useSystemContext();
  const account = product.billingAccount;
  return (
    <span data-ui="ui.product.header">
      <Id prefix="PRD-" id={product.id ?? ''} /> ·{' '}
      <span className="bss-mono">{line?.msisdn ?? '—'}</span> ·{' '}
      <StatusPill kind="product" status={product.status ?? ''} /> · Starts{' '}
      {day(product.startDate, timeZone)}
      {product.terminationDate !== undefined && (
        <> · Ends {day(product.terminationDate, timeZone)}</>
      )}
      {account?.id !== undefined && (
        <>
          {' '}
          ·{' '}
          <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId: account.id })}>
            {account.name ?? accountLabel(account.id)}
          </Link>
        </>
      )}
    </span>
  );
}

/** The card row's menu and dialogs for this product (the same items and requests). */
function Actions({
  account,
  line,
  onDone,
  onRefresh,
}: {
  account: Account;
  line: AccountProduct;
  onDone: (message: string) => void;
  onRefresh: () => void;
}) {
  const context = useSystemContext();
  const [dialog, setDialog] = useState<
    | { kind: 'change'; change: ProductAction; immediate: boolean }
    | { kind: 'number' | 'pin' | 'device' | 'e911' }
    | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const close = () => setDialog(null);
  const finished = (message: string) => {
    setDialog(null);
    onDone(message);
  };
  const name = line.productOffering.name;

  async function undo(orderId: string) {
    setBusy(true);
    setProblem(null);
    try {
      await undoPendingChange(orderId);
      onDone(`${name}: the status change was cancelled`);
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  const ending = line.status === 'pendingTerminate';
  return (
    <>
      {ending ? (
        <Menu
          label="Subscriber number"
          items={[{ label: 'Subscriber number', onClick: () => setDialog({ kind: 'number' }) }]}
        />
      ) : (
        <ProductActionsMenu
          undoable={line.undoableStatusChange}
          busy={busy}
          onUndo={() => {
            if (line.undoableStatusChange) void undo(line.undoableStatusChange.productOrder.id);
          }}
          items={offeredChanges(line, account.ratingType)}
          onPick={(change) => setDialog({ kind: 'change', change, immediate: false })}
          extra={productMenuExtra(line, account.ratingType, context.businessDate, {
            onNumber: () => setDialog({ kind: 'number' }),
            onTransferPin: () => setDialog({ kind: 'pin' }),
            onDevice: () => setDialog({ kind: 'device' }),
            onE911: () => setDialog({ kind: 'e911' }),
            onDeactivate: () => setDialog({ kind: 'change', change: 'end', immediate: true }),
          })}
        />
      )}
      {problem !== null && <Note tone="negative">{problem}</Note>}
      {dialog?.kind === 'change' && dialog.change === 'offering' && (
        <ChangeOfferingDialog
          account={account}
          product={line}
          onClose={close}
          onRefresh={onRefresh}
          onDone={finished}
        />
      )}
      {dialog?.kind === 'change' && dialog.change !== 'offering' && (
        <ProductChangeDialog
          change={dialog.change}
          immediateEnd={dialog.immediate}
          account={account}
          product={line}
          onClose={close}
          onRefresh={onRefresh}
          onDone={finished}
        />
      )}
      {dialog?.kind === 'number' && (
        <NumberModal accountId={account.id} productId={line.id} onClose={close} onDone={finished} />
      )}
      {dialog?.kind === 'pin' && <TransferPinDialog productId={line.id} onClose={close} />}
      {dialog?.kind === 'device' && (
        <DeviceDialog
          accountId={account.id}
          productId={line.id}
          canChange={['active', 'suspended'].includes(line.status)}
          onClose={close}
          onDone={finished}
        />
      )}
      {dialog?.kind === 'e911' && (
        <E911Dialog
          accountId={account.id}
          product={line}
          onClose={close}
          onRefresh={onRefresh}
          onDone={finished}
        />
      )}
    </>
  );
}

function Overview({
  product,
  line,
  lines,
  account,
}: {
  product: InventoryProduct;
  line: AccountProduct | null;
  lines: AccountProduct[];
  account: Account | null;
}) {
  const { timeZone, businessDate: today } = useSystemContext();
  const id = product.id ?? '';
  const addOns = lines.filter((l) => l.parentProduct?.id === id);
  const plan = line?.parentProduct ?? null;
  const facts: [ReactNode, ReactNode][] = [
    [
      'Price today',
      line === null || line.price === null ? (
        <span className="bss-dash">—</span>
      ) : (
        <>
          <Amount cents={toCents(line.price.value)} currency={line.price.unit} />{' '}
          <span className="bss-secondary">/ {line.recurringChargePeriod ?? 'mo'}</span>
        </>
      ),
    ],
    ...(line?.nextChargeDate != null
      ? [['Next charge', line.nextChargeDate] as [string, string]]
      : []),
    ...(line?.paidThrough != null && line.paidThrough >= today
      ? [['Paid through', line.paidThrough] as [string, string]]
      : []),
    ['Specification', product.productSpecification?.name ?? '—'],
    ['Ordered', day(product.orderDate, timeZone)],
    ['Created', day(product.creationDate, timeZone)],
  ];
  return (
    <div data-ui="ui.product.overview">
      <Panel title="Product">
        <KeyValue items={facts} />
        {line !== null && <PlanFacts product={line} />}
        {line !== null && account?.ratingType === 'prepaid' && <RenewalHistory product={line} />}
        {line?.pendingChanges.map((c) => (
          <p key={c.productOrder.id} className="bss-secondary" data-ui="ui.product.pendingChange">
            Pending:{' '}
            {c.kind === 'status'
              ? `${c.status === 'suspended' ? 'suspends' : 'resumes'} on ${c.date}`
              : c.kind === 'e911'
                ? `E911 address changes to ${c.e911Address ? e911Text(c.e911Address) : 'a new address'}`
                : `changes to ${c.productOffering?.name ?? 'another offering'} on ${c.date}`}
          </p>
        ))}
      </Panel>
      {(plan !== null || addOns.length > 0) && (
        <Panel title={plan !== null ? 'Plan' : 'Add-on packs'}>
          <ul data-ui="ui.product.related">
            {plan !== null && (
              <li>
                <Link className="bss-link" href={hrefOf({ name: 'product', productId: plan.id })}>
                  {lines.find((l) => l.id === plan.id)?.productOffering.name ??
                    `PRD-${shortId(plan.id)}`}
                </Link>
              </li>
            )}
            {addOns.map((a) => (
              <li key={a.id}>
                <Link className="bss-link" href={hrefOf({ name: 'product', productId: a.id })}>
                  {a.productOffering.name}
                </Link>{' '}
                <span className="bss-secondary">
                  {a.status}
                  {a.terminationDate !== null && ` · ends ${a.terminationDate}`}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <Resources product={product} />
      {characteristicOf(product, 'imei') !== undefined && (
        <Panel title="Device">
          <p data-ui="ui.products.device">
            IMEI <span className="bss-mono">{characteristicOf(product, 'imei')}</span> ·{' '}
            {characteristicOf(product, 'deviceCompatibility') === 'compatible'
              ? 'works on the network'
              : 'not confirmed by the network'}
          </p>
          {characteristicOf(product, 'deviceReasons') !== undefined && (
            <Note tone="warning">{characteristicOf(product, 'deviceReasons')}</Note>
          )}
        </Panel>
      )}
      {id !== '' && <NetworkCommands owner={{ productId: id }} />}
    </div>
  );
}

/** The numbers and SIM the product holds today, each linking to the Numbers list (#477). */
function Resources({ product }: { product: InventoryProduct }) {
  const resources = product.realizingResource ?? [];
  const ids = resources.map((r) => r.id ?? '').filter(Boolean);
  const numbers = useResource(`product-resources:${ids.join(',')}`, (signal) =>
    loadRealizingNumbers(ids, signal),
  );
  return (
    <Panel title="Number and SIM">
      <section data-ui="ui.products.resource">
        {resources.length === 0 && <p>None.</p>}
        {resources.length > 0 && numbers.status === 'loading' && <p>Loading numbers…</p>}
        {numbers.status === 'error' && (
          <ErrorBlock
            title="Failed to load the product's numbers"
            error={numbers.error}
            onRetry={numbers.retry}
          />
        )}
        {numbers.status === 'ok' &&
          resources.map((r) => {
            const n = r.id !== undefined ? numbers.data[r.id] : null;
            if (!n) {
              return (
                <p key={r.id}>
                  {r['@referredType'] ?? 'Resource'} {r.id !== undefined ? shortId(r.id) : ''}
                </p>
              );
            }
            const iccid = iccidOf(n);
            return (
              <p key={r.id}>
                {kindOf(n) === 'imsi' ? 'IMSI' : 'MSISDN'}{' '}
                <Link className="bss-mono" href={numberHref(n)}>
                  {n.value}
                </Link>
                {iccid !== undefined && (
                  <>
                    {' '}
                    · SIM (ICCID) <span className="bss-mono">{iccid}</span>
                  </>
                )}
              </p>
            );
          })}
      </section>
    </Panel>
  );
}

function ProductCharges({
  productId,
  timeZone,
  onChanged,
}: {
  productId: string;
  timeZone: string;
  onChanged: (message: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [bill, setBill] = useState<string | null>(null);
  const rates = useResource(`product-rates:${productId}`, (signal) =>
    loadProductRates(productId, signal),
  );
  const instant = (value?: string) => (value === undefined ? '' : businessDate(value, timeZone));
  return (
    <section data-ui="ui.product.charges">
      <Panel title="Charges" subtitle="Applied rates of this product, newest first">
        <TableHead columns={CHARGE_COLUMNS} />
        <ListState
          resource={rates}
          columns={CHARGE_COLUMNS}
          errorTitle="Failed to load charges"
          empty={{ title: 'No charges', text: 'The billing run has charged nothing for it yet.' }}
          count={(data) => data.length}
          rows={(data) => data.slice(offset, offset + CHARGE_PAGE)}
          onResetFilters={() => {}}
        >
          {(rows: RateRow[], data) => (
            <>
              {rows.map((rate) => (
                <TableRow
                  key={rate.id}
                  columns={CHARGE_COLUMNS}
                  cells={{
                    date: instant(rate.date) || '—',
                    charge: rate.name ?? rate.appliedBillingRateType ?? '—',
                    period: rate.periodCoverage
                      ? `${instant(rate.periodCoverage.startDateTime)} – ${instant(
                          rate.periodCoverage.endDateTime,
                        )}`
                      : '—',
                    amount: rate.taxExcludedAmount
                      ? formatAmount(rate.taxExcludedAmount.value)
                      : '—',
                    total: rate.taxIncludedAmount
                      ? formatAmount(rate.taxIncludedAmount.value)
                      : '—',
                    bill:
                      rate.bill !== undefined ? (
                        <button
                          type="button"
                          className="bss-link"
                          data-ui="ui.product.charges.bill"
                          onClick={() => setBill(rate.bill?.id ?? null)}
                        >
                          {rate.bill.name ?? shortId(rate.bill.id)}
                        </button>
                      ) : (
                        <span className="bss-secondary">Not billed</span>
                      ),
                  }}
                />
              ))}
              <PageFooter
                offset={offset}
                shown={rows.length}
                more={offset + CHARGE_PAGE < data.length}
                size={CHARGE_PAGE}
                onOffset={setOffset}
                ui="ui.product.charges"
              />
            </>
          )}
        </ListState>
      </Panel>
      {bill !== null && (
        <BillDialog
          billId={bill}
          onClose={() => setBill(null)}
          onChanged={(message) => {
            setBill(null);
            onChanged(message);
          }}
        />
      )}
    </section>
  );
}

function historyText(entry: ProductHistoryEntry): string {
  switch (entry.kind) {
    case 'statusChange':
      return `${entry.status === 'suspended' ? 'Suspended' : 'Active again'} ${
        CAUSE[entry.cause ?? ''] ?? ''
      }`.trim();
    case 'offeringChange':
      return `Offering changed to ${entry.productOffering?.name ?? 'another offering'}`;
    case 'numberAssignment': {
      const r = entry.resource;
      const what =
        r?.kind === 'imsi'
          ? `SIM: IMSI ${r.value}${r.iccid ? `, ICCID ${r.iccid}` : ''}`
          : `Number ${r?.value ?? ''}`;
      return entry.endDate === null ? `${what} assigned` : `${what} held until ${entry.endDate}`;
    }
  }
}

function ProductHistory({ product, productId }: { product: InventoryProduct; productId: string }) {
  const [offset, setOffset] = useState(0);
  const history = useResource(`product-history:${productId}:${offset}`, (signal) =>
    loadProductHistory(productId, offset, signal),
  );
  const items = product.productOrderItem ?? [];
  const orders = ordersHref(product);
  return (
    <section data-ui="ui.product.history">
      <Panel title="Order items">
        {items.length === 0 ? (
          <p>None.</p>
        ) : (
          <div role="table" aria-label="Order items" data-ui="ui.products.orderItems">
            <TableHead columns={ITEM_COLUMNS} density="compact" />
            {items.map((item) => (
              <TableRow
                key={`${item.orderId}:${item.orderItemId}`}
                columns={ITEM_COLUMNS}
                density="compact"
                cells={{
                  item: `${item.role} · ${item.orderItemAction}`,
                  order: item.orderId !== undefined ? <Id prefix="ORD-" id={item.orderId} /> : '—',
                  open: orders !== null ? <Link href={orders}>Open in Orders</Link> : '',
                }}
              />
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Changes" subtitle="Status, offering, number and SIM, newest first">
        <div role="table" aria-label="Changes">
          <TableHead columns={HISTORY_COLUMNS} />
          <ListState
            resource={history}
            columns={HISTORY_COLUMNS}
            errorTitle="Failed to load the history"
            empty={{
              title: 'No changes',
              text: 'No status, offering, number or SIM change has been recorded.',
            }}
            count={(data) => data.items.length}
            rows={(data) => data.items}
            onResetFilters={() => {}}
          >
            {(rows, data) => (
              <>
                {rows.map((entry, index) => (
                  <TableRow
                    key={`${entry.kind}:${entry.date}:${index}`}
                    columns={HISTORY_COLUMNS}
                    cells={{
                      date: entry.date,
                      change: historyText(entry),
                      order: <Id prefix="ORD-" id={entry.productOrder.id} />,
                    }}
                  />
                ))}
                <PageFooter
                  offset={data.offset}
                  shown={rows.length}
                  more={data.more}
                  size={HISTORY_PAGE}
                  onOffset={setOffset}
                  ui="ui.product.history"
                />
              </>
            )}
          </ListState>
        </div>
      </Panel>
    </section>
  );
}
