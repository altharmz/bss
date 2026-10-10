import { useState, type ReactNode } from 'react';
import {
  loadAccount,
  loadCharges,
  loadCustomerStatus,
  loadProducts,
  loadSettlement,
  loadTransactions,
  orderDelete,
  type Account,
  type AccountProduct,
  type AccountSettlement,
} from '../api/bss';
import { explain } from '../api/client';
import { useResource, type ResourceWithRetry } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { lastDay, nextCycleDate } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { formatCents, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Button, Panel, Pill, useToast } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { AddProductModal } from './AddProduct';
import { BillDialog, BillsPanel, RecordPaymentModal, useBills } from './Bills';
import './screens.css';

// Screen 2. Everything about one billing account. Products change only through TMF622
// orders: Add product posts an add item, Deactivate a delete item; both reload the card.
// Bills: Record payment (TMF676) and a bill's hold/release (TMF678 PATCH) reload it too.

const PRODUCT_COLUMNS: Column[] = [
  { key: 'offering', label: 'Offering', width: '1fr' },
  { key: 'activated', label: 'Activated', width: '140px', mono: true },
  { key: 'next', label: 'Next charge', width: '170px', mono: true },
  { key: 'price', label: 'Price', width: '170px', align: 'right', mono: true },
  { key: 'actions', label: 'Actions', width: '170px' },
];
const CHARGE_COLUMNS: Column[] = [
  { key: 'date', label: 'Date', width: '92px', mono: true },
  { key: 'period', label: 'Period', width: '1fr' },
  { key: 'days', label: 'Days', width: '84px', mono: true },
  { key: 'amount', label: 'Amount', width: '110px', align: 'right', mono: true },
];
const TX_COLUMNS: Column[] = [
  { key: 'date', label: 'Date', width: '92px', mono: true },
  { key: 'type', label: 'Type', width: '1fr' },
  { key: 'amount', label: 'Amount', width: '116px', align: 'right', mono: true },
  { key: 'after', label: 'Balance after', width: '116px', align: 'right', mono: true },
];

const KPI_SKELETON: Column[] = ['1.3fr', '.7fr', '1fr', '1fr', '1fr'].map((width, i) => ({
  key: `kpi${i}`,
  label: '',
  width,
}));

const TYPE_LABEL: Record<string, string> = {
  charge: 'Charge',
  refund: 'Refund',
  payment: 'Payment',
};

export function SubscriberCardScreen({ accountId }: { accountId: string }) {
  const [version, setVersion] = useState(0); // bumped after an order: reload everything
  const account = useResource(`account:${accountId}:${version}`, (signal) =>
    loadAccount(accountId, signal),
  );
  const crumb = account.status === 'ok' ? account.data.customerName : accountLabel(accountId);
  useSetCrumbs(
    [{ label: 'Subscribers', href: hrefOf({ name: 'subscribers' }) }, { label: crumb }],
    `subscriber:${accountId}:${crumb}`,
  );

  return (
    <div className="bss-screen" data-ui="ui.subscriber.card">
      <Link className="bss-link bss-back" href={hrefOf({ name: 'subscribers' })}>
        ← Back to subscribers
      </Link>
      {account.status === 'loading' && (
        <Panel title="Billing account">
          {/* skeleton KPI blocks, laid out like the KPI grid */}
          <SkeletonRows columns={KPI_SKELETON} rows={2} />
        </Panel>
      )}
      {account.status === 'error' && (
        <Panel>
          <ErrorBlock
            title="Failed to load the billing account"
            error={account.error}
            onRetry={account.retry}
          />
        </Panel>
      )}
      {account.status === 'ok' && (
        <Card account={account.data} version={version} reload={() => setVersion((v) => v + 1)} />
      )}
    </div>
  );
}

function Card({
  account,
  version,
  reload,
}: {
  account: Account;
  version: number;
  reload: () => void;
}) {
  const context = useSystemContext();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [paying, setPaying] = useState(false);
  const [openBill, setOpenBill] = useState<string | null>(null);
  const customerStatus = useResource(`customer:${account.customerId}:${version}`, (signal) =>
    loadCustomerStatus(account.customerId, signal),
  );
  const products = useResource(`products:${account.id}:${version}`, (signal) =>
    loadProducts(account.id, signal),
  );
  const settlement = useResource(`settlement:${account.id}:${version}`, (signal) =>
    loadSettlement(account.id, signal),
  );
  const bills = useBills(account, version);
  const canAdd = account.state === 'Active';
  const activeOfferings = new Set(
    products.status === 'ok' ? products.data.map((p) => p.productOffering.id) : [],
  );
  const addButton = (
    <Button variant="primary" disabled={!canAdd} onClick={() => setAdding(true)}>
      + Add product
    </Button>
  );

  return (
    <>
      <Panel>
        <div className="bss-card-header">
          <div>
            <h1>
              {account.customerName} <Pill status={account.state} />
            </h1>
            <div className="bss-meta">
              <span>
                Subscriber no. <span className="value">{shortId(account.customerId)}</span>
              </span>
              <span>
                Billing account <span className="value">{accountLabel(account.id)}</span>
              </span>
              <span>
                Customer{' '}
                <span className="value">
                  {customerStatus.status === 'ok' ? customerStatus.data : '…'}
                </span>
              </span>
            </div>
          </div>
          <div className="bss-card-action">
            <Button onClick={() => setPaying(true)} disabled={bills.status !== 'ok'}>
              Record payment
            </Button>{' '}
            {addButton}
            {!canAdd && (
              <div className="caption">Unavailable for {account.state.toLowerCase()} accounts</div>
            )}
          </div>
        </div>
      </Panel>

      <BillingAccountPanel
        account={account}
        today={context.businessDate}
        settlement={settlement.status === 'ok' ? settlement.data : null}
      />

      <Panel
        title="Active products"
        right={
          products.status === 'ok' ? (
            <span className="bss-panel-count">{products.data.length}</span>
          ) : undefined
        }
      >
        <TableHead columns={PRODUCT_COLUMNS} />
        <ListState
          resource={products}
          columns={PRODUCT_COLUMNS}
          errorTitle="Failed to load products"
          empty={{
            title: 'No active products',
            text: 'Add an offering from the catalog to start charging.',
            ...(canAdd ? { action: addButton } : {}),
          }}
          total={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {}}
        >
          {(rows) =>
            rows.map((product) => (
              <ProductRow
                key={product.id}
                account={account}
                product={product}
                onDone={(message) => {
                  toast(message);
                  reload();
                }}
              />
            ))
          }
        </ListState>
      </Panel>

      <BillsPanel bills={bills} onOpen={(bill) => setOpenBill(bill.id)} />

      <div className="bss-two-col">
        <History account={account} version={version} />
      </div>

      {paying && bills.status === 'ok' && (
        <RecordPaymentModal
          account={account}
          bills={bills.data}
          onClose={() => setPaying(false)}
          onRecorded={(message) => {
            setPaying(false);
            toast(message);
            reload();
          }}
        />
      )}
      {openBill !== null && (
        <BillDialog
          billId={openBill}
          onClose={() => setOpenBill(null)}
          onChanged={(message) => {
            setOpenBill(null); // the card reloads as a whole, as after any change
            toast(message);
            reload();
          }}
        />
      )}

      {adding && (
        <AddProductModal
          account={account}
          activeOfferingIds={activeOfferings}
          onClose={() => setAdding(false)}
          onOrdered={(message) => {
            setAdding(false);
            toast(message);
            reload();
          }}
        />
      )}
    </>
  );
}

function BillingAccountPanel({
  account,
  today,
  settlement,
}: {
  account: Account;
  today: string;
  settlement: AccountSettlement | null;
}) {
  const negative = account.balanceCents < 0;
  const available =
    account.creditLimitCents === null ? null : account.balanceCents + account.creditLimitCents;
  return (
    <Panel
      title="Billing account"
      right={
        <span className="bss-secondary">
          BillingAccount <span className="num">{accountLabel(account.id)}</span>
        </span>
      }
    >
      <div className="bss-kpis">
        <div className="bss-kpi">
          <div className="label">Balance</div>
          <div className={`value strong${negative ? ' negative' : ''}`}>
            {formatCents(account.balanceCents)}
          </div>
          {negative && <div className="caption negative">In arrears</div>}
        </div>
        <div className="bss-kpi">
          <div className="label">Currency</div>
          <div className="value">{account.currency}</div>
        </div>
        <div className="bss-kpi">
          <div className="label">Billing cycle day</div>
          <div className="value">Day {account.cycleDay}</div>
          <div className="caption">next cycle {nextCycleDate(today, account.cycleDay)}</div>
        </div>
        <div className="bss-kpi">
          <div className="label">Credit limit</div>
          <div className="value">
            {account.creditLimitCents === null ? '—' : formatCents(account.creditLimitCents)}
          </div>
        </div>
        <div className="bss-kpi">
          <div className="label">Available incl. credit</div>
          <div className={`value${available !== null && available < 0 ? ' negative' : ''}`}>
            {available === null ? '—' : formatCents(available)}
          </div>
        </div>
        <div className="bss-kpi">
          <div className="label">Due on bills</div>
          <div className="value">
            {settlement === null ? '…' : formatCents(toCents(settlement.outstanding.value))}
          </div>
          {settlement !== null && settlement.overdue.count > 0 && (
            <div className="caption negative">
              {settlement.overdue.count} overdue ·{' '}
              {formatCents(toCents(settlement.overdue.amount.value))}
            </div>
          )}
          {settlement !== null && settlement.onHold.count > 0 && (
            <div className="caption">{settlement.onHold.count} on hold</div>
          )}
        </div>
        <div className="bss-kpi">
          <div className="label">Unused credit</div>
          <div className="value">
            {settlement === null ? '…' : formatCents(toCents(settlement.unallocatedCredit.value))}
          </div>
        </div>
      </div>
    </Panel>
  );
}

function statusNote(product: AccountProduct): { status: string; label: string } | null {
  switch (product.status) {
    case 'active':
      return null;
    case 'created':
    case 'pendingActive':
      return { status: product.status, label: `starts ${product.startDate}` };
    case 'pendingTerminate':
      return { status: product.status, label: `ends ${product.terminationDate ?? ''}` };
    default:
      return { status: product.status, label: product.status };
  }
}

function ProductRow({
  account,
  product,
  onDone,
}: {
  account: Account;
  product: AccountProduct;
  onDone: (message: string) => void;
}) {
  const context = useSystemContext();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const note = statusNote(product);
  const ending = product.status === 'pendingTerminate';
  const name = product.productOffering.name;

  async function deactivate() {
    setBusy(true);
    setProblem(null);
    try {
      const result = await orderDelete(account.id, product.id);
      if (result.state === 'completed') {
        onDone(
          product.startDate > context.businessDate
            ? `${name} cancelled before its start`
            : `${name} deactivated as of ${context.businessDate}`,
        );
      } else {
        setProblem(`Rejected: ${result.reasons.join(' ') || result.state}`);
        setConfirming(false);
      }
    } catch (error) {
      setProblem(explain(error));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  const code = [product.productOffering.code, product.productSpecification.name]
    .filter(Boolean)
    .join(' · ');
  return (
    <div className="bss-product-row">
      <TableRow
        columns={PRODUCT_COLUMNS}
        style={{ minHeight: 44 }}
        cells={{
          offering: (
            <div>
              <span className="bss-name">{name}</span>
              {note && (
                <>
                  {' '}
                  <Pill status={note.status} label={note.label} />
                </>
              )}
              <div className="bss-code">{code}</div>
              {problem !== null && (
                <div className="bss-inline-error" role="alert">
                  {problem}
                </div>
              )}
            </div>
          ),
          activated: product.startDate,
          next: product.nextChargeDate ?? <span className="bss-dash">—</span>,
          price:
            product.price === null ? (
              <span className="bss-dash">—</span>
            ) : (
              <>
                <Amount cents={toCents(product.price.value)} currency={product.price.unit} />{' '}
                <span className="bss-secondary">/ mo</span>
              </>
            ),
          actions: ending ? null : confirming ? (
            <span className="bss-confirm">
              <span className="ask">Deactivate?</span>
              <Button variant="danger" small disabled={busy} onClick={() => void deactivate()}>
                Yes
              </Button>
              <Button small disabled={busy} onClick={() => setConfirming(false)}>
                No
              </Button>
            </span>
          ) : (
            <Button
              variant="danger-outline"
              small
              data-ui="ui.subscriber.deactivate"
              onClick={() => setConfirming(true)}
            >
              Deactivate
            </Button>
          ),
        }}
      />
    </div>
  );
}

function History({ account, version }: { account: Account; version: number }) {
  const charges = useResource(`charges:${account.id}:${version}`, (signal) =>
    loadCharges(account.id, signal),
  );
  const transactions = useResource(`transactions:${account.id}:${version}`, (signal) =>
    loadTransactions(account.id, signal),
  );
  return (
    <>
      <Panel title="Charge history" subtitle="AppliedCustomerBillingRate">
        <TableHead columns={CHARGE_COLUMNS} />
        <SimpleList
          resource={charges}
          columns={CHARGE_COLUMNS}
          errorTitle="Failed to load charges"
          empty={{ title: 'No charges', text: 'Charges appear after the first billing run.' }}
          render={(c) => ({
            date: c.runDate,
            period: (
              <div>
                <span className="num">
                  {c.period.start} – {lastDay(c.period.end)}
                </span>
                <div className="bss-secondary">{c.productOffering.name}</div>
              </div>
            ),
            days: c.prorated ? (
              <span className="bss-prorated">
                {c.billableDays} of {c.periodDays}
              </span>
            ) : (
              <span className="bss-dash">—</span>
            ),
            amount: <Amount cents={toCents(c.amount.value)} />,
          })}
        />
      </Panel>
      <Panel title="Transaction history" subtitle={account.currency}>
        <TableHead columns={TX_COLUMNS} />
        <SimpleList
          resource={transactions}
          columns={TX_COLUMNS}
          errorTitle="Failed to load transactions"
          empty={{ title: 'No transactions', text: 'No payments or charges on this account.' }}
          render={(t) => ({
            date: t.date,
            type: (
              <div>
                {TYPE_LABEL[t.type] ?? t.type}
                <div className="bss-secondary">{t.note}</div>
              </div>
            ),
            amount: <Amount cents={toCents(t.amount.value)} signed strong />,
            after: <Amount cents={toCents(t.balanceAfter.value)} />,
          })}
        />
      </Panel>
    </>
  );
}

function SimpleList<T extends { id: string }>({
  resource,
  columns,
  errorTitle,
  empty,
  render,
}: {
  resource: ResourceWithRetry<T[]>;
  columns: Column[];
  errorTitle: string;
  empty: { title: string; text: string };
  render: (row: T) => Record<string, ReactNode>;
}) {
  return (
    <ListState
      resource={resource}
      columns={columns}
      errorTitle={errorTitle}
      empty={empty}
      total={(data) => data.length}
      rows={(data) => data}
      onResetFilters={() => {}}
    >
      {(rows) => rows.map((row) => <TableRow key={row.id} columns={columns} cells={render(row)} />)}
    </ListState>
  );
}
