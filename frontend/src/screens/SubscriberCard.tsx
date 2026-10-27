import { Note } from '../ui/Note';
import { useCallback, useState, type ReactNode } from 'react';
import {
  loadAccount,
  loadCharges,
  loadCustomerStatus,
  loadPrepaidBalance,
  loadProducts,
  loadSettlement,
  loadTransactions,
  orderDelete,
  undoDeactivation,
  undoPendingChange,
  type Account,
  type AccountProduct,
  type AccountSettlement,
  type Charge,
  type PrepaidBalance,
  type Transaction,
} from '../api/bss';
import { explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import { useResource, type ResourceWithRetry } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link, useRouter } from '../app/router';
import { lastDay, nextCycleDate } from '../format/dates';
import { accountLabel, shortId } from '../format/ids';
import { formatCents, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import { Button, Menu, Panel, StatusPill, Tag, useToast } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { AddProductModal } from './AddProduct';
import { ProductNumber } from './ProductNumber';
import { BillDialog, RecordPaymentModal, useBills } from './Bills';
import { BalanceActions } from './PrepaidHistory';
import {
  ChangeOfferingDialog,
  offeredChanges,
  ProductActionsMenu,
  ProductChangeDialog,
  type ProductAction,
} from './ProductActions';
import { PrepaidMoneyModal, type MoneyForm } from './PrepaidMoney';
import { FailedCommands, OrderWatch } from './PrepaidOrderWatch';
import { PrepaidTransferModal, TransferWatch } from './PrepaidTransfer';
import { watch, type Watched } from './watched';
import { PaymentsPanel } from './Payments';
import { AccountBills, RefundAction, RefundsPanel } from './AccountActions';
import { DeleteSubscriberModal, EditSubscriberModal } from './SubscriberForms';
import './screens.css';

// Screen 2. Everything about one billing account. Products change only through TMF622
// orders: Add product posts an add item, Deactivate a delete item; both reload the card.
// Bills: Record payment (TMF676) and a bill's hold/release (TMF678 PATCH) reload it too.

const PRODUCT_COLUMNS: Column[] = [
  { key: 'offering', label: 'Offering', width: '1fr' },
  { key: 'activated', label: 'Activated', width: '140px', mono: true },
  { key: 'next', label: 'Next charge', width: '170px', mono: true },
  { key: 'price', label: 'Price', width: '170px', align: 'right', mono: true },
  { key: 'actions', label: 'Actions', width: '300px' },
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
  payout: 'Refund paid out',
  adjustment: 'Adjustment',
  reversal: 'Reversal',
  transferIn: 'Transfer in',
  transferOut: 'Transfer out',
};

export function SubscriberCardScreen({ accountId }: { accountId: string }) {
  const [version, setVersion] = useState(0); // bumped after an order: reload everything
  const toast = useToast();
  // accepted transfers and prepaid orders are followed here, above the card, which unmounts
  // while it reloads
  const [watches, setWatches] = useState<Watched[]>([]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const follow = useCallback((added: Watched) => setWatches((all) => [...all, added]), []);
  const dismiss = useCallback(
    (id: string) => setWatches((all) => all.filter((w) => w.id !== id)),
    [],
  );
  const settled = useCallback(
    (id: string, message: string) => {
      dismiss(id);
      toast(message);
      reload();
    },
    [toast, reload, dismiss],
  );
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
      {watches.map((w) =>
        w.kind === 'transfer' ? (
          <TransferWatch
            key={w.id}
            watched={w}
            onSettled={(message) => settled(w.id, message)}
            onDismiss={() => dismiss(w.id)}
          />
        ) : (
          <OrderWatch
            key={w.id}
            watched={w}
            onSettled={(message) => settled(w.id, message)}
            onDismiss={() => dismiss(w.id)}
          />
        ),
      )}
      {account.status === 'ok' && (
        <Card
          account={account.data}
          version={version}
          reload={reload}
          transferWatched={watches.some((w) => w.kind === 'transfer')}
          onWatch={follow}
        />
      )}
    </div>
  );
}

function Card({
  account,
  version,
  reload,
  transferWatched,
  onWatch,
}: {
  account: Account;
  version: number;
  reload: () => void;
  transferWatched: boolean;
  onWatch: (watched: Watched) => void;
}) {
  const context = useSystemContext();
  const toast = useToast();
  const { navigate } = useRouter();
  const [adding, setAdding] = useState(false);
  const [paying, setPaying] = useState(false);
  const [money, setMoney] = useState<MoneyForm | null>(null);
  const [moneyAmount, setMoneyAmount] = useState<string | undefined>(undefined);
  const [transferring, setTransferring] = useState(false);
  const [openBill, setOpenBill] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const customerStatus = useResource(`customer:${account.customerId}:${version}`, (signal) =>
    loadCustomerStatus(account.customerId, signal),
  );
  const products = useResource(`products:${account.id}:${version}`, (signal) =>
    loadProducts(account.id, signal),
  );
  const settlement = useResource(`settlement:${account.id}:${version}`, (signal) =>
    loadSettlement(account.id, signal),
  );
  const balance = useResource(`prepaid:${account.id}:${version}`, (signal) =>
    account.ratingType === 'prepaid'
      ? loadPrepaidBalance(account.id, signal)
      : Promise.resolve(null),
  );
  const bills = useBills(account, version);
  const charges = useResource(`charges:${account.id}:${version}`, (signal) =>
    loadCharges(account.id, signal),
  );
  const transactions = useResource(`transactions:${account.id}:${version}`, (signal) =>
    loadTransactions(account.id, signal),
  );
  // Delete is offered only when everything the card loaded is empty (a postpaid account:
  // a prepaid bucket is history). The server still decides (409: close it instead).
  const deletable =
    account.ratingType === 'postpaid' &&
    products.status === 'ok' &&
    products.data.length === 0 &&
    charges.status === 'ok' &&
    charges.data.length === 0 &&
    transactions.status === 'ok' &&
    transactions.data.length === 0 &&
    bills.status === 'ok' &&
    bills.data.length === 0;
  const canAdd = account.state === 'Active';
  const activeOfferings = new Set(
    products.status === 'ok' ? products.data.map((p) => p.productOffering.id) : [],
  );
  const addButton = (
    <Button variant="secondary" disabled={!canAdd} onClick={() => setAdding(true)}>
      Add product
    </Button>
  );

  return (
    <>
      <header className="subscriber-header">
        <div className="subscriber-heading-row">
          <h1>{account.customerName}</h1>
          <StatusPill kind="account" status={account.state} />
          <Tag>{account.ratingType.toUpperCase()}</Tag>
          <div className="subscriber-header-actions">
            {addButton}
            <Button onClick={() => setEditing(true)}>Edit</Button>
            <Menu
              label="More actions"
              ui="ui.subscriber.moreActions"
              items={[
                ...(account.ratingType === 'postpaid'
                  ? [
                      {
                        label: 'Record payment',
                        disabled: bills.status !== 'ok',
                        onClick: () => setPaying(true),
                      },
                    ]
                  : []),
                ...(deletable
                  ? [
                      {
                        label: 'Delete…',
                        tone: 'negative' as const,
                        ui: 'ui.subscriber.delete',
                        onClick: () => setDeleting(true),
                      },
                    ]
                  : []),
              ]}
            />
          </div>
        </div>
        {!canAdd && (
          <div className="subscriber-add-unavailable">
            Unavailable for {account.state.toLowerCase()} accounts
          </div>
        )}
        <div className="subscriber-meta">
          <Id prefix="BA-" id={account.id} />
          <span>
            Subscriber no. <span className="num">{shortId(account.customerId)}</span>
          </span>
          <span>
            Customer{' '}
            {customerStatus.status === 'ok' ? (
              <StatusPill kind="customer" status={customerStatus.data} />
            ) : (
              '…'
            )}
          </span>
          {account.financialAccount !== null && (
            <span data-ui="ui.subscriber.financialAccount">
              Financial account{' '}
              <Link className="bss-link" href={hrefOf({ name: 'financialAccounts' })}>
                {account.financialAccount.name}
              </Link>
            </span>
          )}
          <Link
            className="bss-link"
            data-ui="ui.subscriber.orders"
            href={`/orders?account=${account.id}`}
          >
            Orders
          </Link>
          {account.ratingType === 'prepaid' && <span>Charged daily at {context.billingRunAt}</span>}
        </div>
      </header>
      {account.ratingType === 'prepaid' && (
        <div className="subscriber-money-actions">
          <Button onClick={() => setPaying(true)} disabled={bills.status !== 'ok'}>
            Record payment
          </Button>
          {account.ratingType === 'prepaid' &&
            (
              [
                ['topup', 'Top up'],
                ['adjust', 'Adjust'],
                ['hold', 'Hold funds'],
              ] as const
            ).map(([form, label]) => (
              <span key={form}>
                <Button
                  onClick={() => {
                    setMoneyAmount(undefined);
                    setMoney(form);
                  }}
                  disabled={balance.status !== 'ok' || balance.data === null}
                >
                  {label}
                </Button>{' '}
              </span>
            ))}
          {account.ratingType === 'prepaid' && (
            <>
              <Button
                onClick={() => setTransferring(true)}
                disabled={balance.status !== 'ok' || balance.data === null || transferWatched}
              >
                Transfer
              </Button>{' '}
            </>
          )}
        </div>
      )}

      <BillingAccountPanel
        account={account}
        today={context.businessDate}
        settlement={settlement.status === 'ok' ? settlement.data : null}
        onChanged={reload}
        prepaid={
          account.ratingType === 'prepaid' ? (
            <PrepaidFigures accountId={account.id} balance={balance} products={products} />
          ) : null
        }
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
                onRefresh={reload}
                onTopUp={(amount) => {
                  setMoneyAmount(amount);
                  setMoney('topup');
                }}
                onDone={(message, accepted) => {
                  toast(message);
                  if (accepted?.id) onWatch(watch('order', accepted.id, accepted.label));
                  reload();
                }}
              />
            ))
          }
        </ListState>
      </Panel>

      {account.ratingType === 'prepaid' && (
        <FailedCommands accountId={account.id} version={version} onRetried={reload} />
      )}

      <AccountBills
        account={account}
        version={version}
        bills={bills}
        onOpen={setOpenBill}
        onCompleted={reload}
      />
      <PaymentsPanel accountId={account.id} version={version} />
      <RefundsPanel accountId={account.id} version={version} />

      {account.ratingType === 'prepaid' && (
        <BalanceActions
          balance={balance}
          accountId={account.id}
          version={version}
          onChanged={reload}
        />
      )}

      <div className="bss-two-col">
        <History account={account} charges={charges} transactions={transactions} />
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
      {money !== null && balance.status === 'ok' && balance.data !== null && (
        <PrepaidMoneyModal
          form={money}
          account={account}
          balance={balance.data}
          {...(moneyAmount === undefined ? {} : { initialAmount: moneyAmount })}
          onClose={() => setMoney(null)}
          onDone={(message) => {
            setMoney(null);
            toast(message);
            reload();
          }}
        />
      )}
      {transferring && balance.status === 'ok' && balance.data !== null && (
        <PrepaidTransferModal
          account={account}
          balance={balance.data}
          onClose={() => setTransferring(false)}
          onAccepted={(accepted) => {
            setTransferring(false);
            onWatch(accepted);
            reload();
          }}
        />
      )}
      {deleting && (
        <DeleteSubscriberModal
          account={account}
          onClose={() => setDeleting(false)}
          onDeleted={(message) => {
            toast(message);
            navigate(hrefOf({ name: 'subscribers' }));
          }}
        />
      )}
      {editing && (
        <EditSubscriberModal
          account={account}
          onClose={() => setEditing(false)}
          onSaved={(message) => {
            setEditing(false);
            toast(message);
            reload();
          }}
        />
      )}
      {openBill !== null && (
        <BillDialog
          billId={openBill}
          cycleId={
            bills.status === 'ok' ? bills.data.find((b) => b.id === openBill)?.cycleId : undefined
          }
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
          onTopUp={(amount) => {
            setAdding(false);
            setMoneyAmount(amount);
            setMoney('topup');
          }}
          onOrdered={(message, accepted) => {
            setAdding(false);
            toast(message);
            if (accepted?.id) onWatch(watch('order', accepted.id, accepted.label));
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
  onChanged,
  prepaid,
}: {
  account: Account;
  today: string;
  settlement: AccountSettlement | null;
  onChanged: () => void;
  prepaid: ReactNode;
}) {
  const negative = account.balanceCents < 0;
  const available =
    account.creditLimitCents === null ? null : account.balanceCents + account.creditLimitCents;
  return (
    <Panel title="Billing account">
      <div className="bss-kpis">
        <div className="bss-kpi">
          <div className="label">{prepaid ? 'Ledger' : 'Balance'}</div>
          <div className={`value strong${negative ? ' negative' : ''}`}>
            {formatCents(account.balanceCents)}
          </div>
          {negative && (
            <div className="caption negative">{prepaid ? 'Below zero' : 'In arrears'}</div>
          )}
          {prepaid && <div className="caption">Prepaid</div>}
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
        {prepaid ? (
          prepaid
        ) : (
          <>
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
          </>
        )}
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
          <RefundAction account={account} settlement={settlement} onChanged={onChanged} />
        </div>
      </div>
    </Panel>
  );
}

const CAUSE_LABEL: Record<string, string> = {
  funding: 'funding ran out',
  manual: 'suspended by the operator',
  account: 'the account is not active',
};

/** The prepaid KPIs. Ledger (the account's), available (ledger less holds, from the TMF654
 * bucket), reserved (holds) and paid-through / shortfall (per product, from the account's
 * product view) are four different things and never merged. */
function PrepaidFigures({
  accountId,
  balance,
  products,
}: {
  accountId: string;
  balance: ResourceWithRetry<PrepaidBalance | null>;
  products: ResourceWithRetry<AccountProduct[]>;
}) {
  const figures = balance.status === 'ok' ? balance.data : null;
  const rows = products.status === 'ok' ? products.data : null;
  const dates = (rows ?? []).flatMap((p) => (p.paidThrough ? [p.paidThrough] : [])).sort();
  const short = (rows ?? []).filter((p) => p.needs_topup);
  const worst = short.reduce<AccountProduct | null>(
    (best, p) =>
      best === null ||
      toCents(p.needs_topup?.shortfall ?? 0) > toCents(best.needs_topup?.shortfall ?? 0)
        ? p
        : best,
    null,
  );
  const causes = [
    ...new Set((rows ?? []).flatMap((p) => (p.suspensionCause ? [p.suspensionCause] : []))),
  ];
  return (
    <>
      <div className="bss-kpi" data-ui="ui.subscriber.prepaid.available">
        <div className="label">Available</div>
        <div
          className={`value${figures !== null && figures.availableCents < 0 ? ' negative' : ''}`}
        >
          {balance.status === 'error'
            ? '—'
            : figures === null
              ? '…'
              : formatCents(figures.availableCents)}
        </div>
        <div className="caption">ledger less holds</div>
      </div>
      <div className="bss-kpi" data-ui="ui.subscriber.prepaid.reserved">
        <div className="label">Reserved</div>
        <div className="value">
          {balance.status === 'error'
            ? '—'
            : figures === null
              ? '…'
              : formatCents(figures.reservedCents)}
        </div>
        <div className="caption">holds not spent yet</div>
        {balance.status === 'error' && (
          <Note tone="negative">Failed to load the prepaid balance ({balance.error.message})</Note>
        )}
      </div>
      <div className="bss-kpi" data-ui="ui.subscriber.prepaid.paidThrough">
        <div className="label">Paid through</div>
        <div className="value">{rows === null ? '…' : (dates[0] ?? '—')}</div>
        <div className="caption">earliest of its products, service ends here</div>
      </div>
      <div className="bss-kpi" data-ui="ui.subscriber.prepaid.shortfall">
        <div className="label">Shortfall</div>
        <div className={`value${worst ? ' negative' : ''}`}>
          {rows === null
            ? '…'
            : worst?.needs_topup
              ? formatCents(toCents(worst.needs_topup.shortfall))
              : 'None'}
        </div>
        {worst?.needs_topup && (
          <div className="caption negative">
            needs top-up: renewal needs {formatCents(toCents(worst.needs_topup.required))},
            available {formatCents(toCents(worst.needs_topup.available))}
            {short.length > 1 ? ` · ${short.length} products` : ''}
          </div>
        )}
        {causes.length > 0 && (
          <div className="caption">
            Suspension: {causes.map((c) => CAUSE_LABEL[c] ?? c).join(', ')}
          </div>
        )}
        <div className="caption">
          <Link
            className="bss-link"
            data-ui="ui.subscriber.prepaid.commands"
            href={`/prepaid-commands?account=${accountId}`}
          >
            Commands
          </Link>
        </div>
      </div>
    </>
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
  onRefresh,
  onTopUp,
}: {
  account: Account;
  product: AccountProduct;
  onDone: (message: string, accepted?: PendingOrder) => void;
  onRefresh: () => void;
  onTopUp: (amount: string) => void;
}) {
  const context = useSystemContext();
  const [changing, setChanging] = useState<ProductAction | null>(null);
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
      } else if (result.state === 'inProgress') {
        // a prepaid account: the billing worker ends the product (OrderWatch follows)
        onDone(`${name}: deactivation accepted, the billing worker completes it shortly`, {
          id: result.id,
          label: `Deactivation of ${name}`,
        });
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

  async function undo() {
    setBusy(true);
    setProblem(null);
    try {
      await undoDeactivation(product.id);
      onDone(`${name} stays active: the deactivation was cancelled`);
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  async function undoChange(orderId: string) {
    setBusy(true);
    setProblem(null);
    try {
      await undoPendingChange(orderId);
      onDone(`${name}: the pending change was cancelled`);
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  const fundingWait =
    account.ratingType === 'prepaid' &&
    product.status === 'suspended' &&
    product.suspensionCause === 'funding' &&
    product.needs_topup
      ? product.needs_topup
      : null;
  const code = [product.productOffering.code, product.productSpecification?.name]
    .filter(Boolean)
    .join(' · ');
  return (
    <div className="bss-product-row">
      <TableRow
        columns={PRODUCT_COLUMNS}
        className="product-row-height"
        cells={{
          offering: (
            <div>
              <span className="bss-name">{name}</span>
              {note && (
                <>
                  {' '}
                  <StatusPill kind="product" status={note.status} label={note.label} />
                </>
              )}
              <div className="bss-code">{code}</div>
              <ProductNumber accountId={account.id} productId={product.id} onDone={onDone} />
              {product.pendingChanges.map((c) => (
                <div
                  key={c.productOrder.id}
                  className="bss-secondary"
                  data-ui="ui.subscriber.pendingChange"
                >
                  {c.kind === 'status'
                    ? c.status === 'suspended'
                      ? `Suspends on ${c.date}`
                      : `Resumes on ${c.date}`
                    : `Changes to ${c.productOffering?.name ?? 'another offering'} on ${c.date}${
                        account.ratingType === 'prepaid' && c.date < context.businessDate
                          ? ' (waits for funds)'
                          : ''
                      }`}{' '}
                  <Button
                    small
                    disabled={busy}
                    data-ui="ui.subscriber.undoChange"
                    onClick={() => void undoChange(c.productOrder.id)}
                  >
                    Undo
                  </Button>
                </div>
              ))}
              {fundingWait !== null && (
                <div className="bss-secondary">
                  Waiting for funds: short {formatCents(toCents(fundingWait.shortfall))}
                </div>
              )}
              {problem !== null && <Note tone="negative">{problem}</Note>}
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
                <span className="bss-secondary">/ {product.recurringChargePeriod ?? 'mo'}</span>
              </>
            ),
          actions: ending ? (
            <Button
              small
              disabled={busy}
              data-ui="ui.subscriber.undoDeactivation"
              onClick={() => void undo()}
            >
              Undo deactivation
            </Button>
          ) : confirming ? (
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
            <>
              <ProductActionsMenu
                items={offeredChanges(product, account.ratingType)}
                onPick={(change) => setChanging(change)}
              />{' '}
              {fundingWait !== null && (
                <>
                  <Button
                    small
                    data-ui="ui.subscriber.topUpProduct"
                    onClick={() =>
                      onTopUp(formatCents(toCents(fundingWait.shortfall)).replace(/,/g, ''))
                    }
                  >
                    Top up
                  </Button>{' '}
                </>
              )}
              <Button
                variant="quiet"
                tone="negative"
                small
                data-ui="ui.subscriber.deactivate"
                onClick={() => setConfirming(true)}
              >
                Deactivate
              </Button>
            </>
          ),
        }}
      />
      {changing === 'offering' && (
        <ChangeOfferingDialog
          account={account}
          product={product}
          onClose={() => setChanging(null)}
          onRefresh={onRefresh}
          onDone={(message, accepted) => {
            setChanging(null);
            onDone(message, accepted);
          }}
        />
      )}
      {changing !== null && changing !== 'offering' && (
        <ProductChangeDialog
          change={changing}
          account={account}
          product={product}
          onClose={() => setChanging(null)}
          onRefresh={onRefresh}
          onDone={(message, accepted) => {
            setChanging(null);
            onDone(message, accepted);
          }}
        />
      )}
    </div>
  );
}

function History({
  account,
  charges,
  transactions,
}: {
  account: Account;
  charges: ResourceWithRetry<Charge[]>;
  transactions: ResourceWithRetry<Transaction[]>;
}) {
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
                <div className="bss-secondary">{c.productOffering?.name ?? 'Transfer fee'}</div>
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
          keyOf={(t) => `${t.type}:${t.id}`}
          render={(t) => ({
            date: t.date,
            type: (
              <div>
                {TYPE_LABEL[t.type] ?? t.type}
                <div className="bss-secondary">{t.note}</div>
              </div>
            ),
            amount: (
              <span className="amount-strong">
                <Amount cents={toCents(t.amount.value)} signed />
              </span>
            ),
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
  keyOf = (row) => row.id,
}: {
  keyOf?: (row: T) => string;
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
      {(rows) =>
        rows.map((row) => <TableRow key={keyOf(row)} columns={columns} cells={render(row)} />)
      }
    </ListState>
  );
}
