import { writeLock } from '../api/WriteLock';
import { loadBalanceAction } from '../api/history';
import { Note } from '../ui/Note';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  loadAccount,
  loadCharges,
  loadCustomerStatus,
  loadPrepaidBalance,
  loadProducts,
  loadSettlement,
  loadTransactions,
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
import {
  Badge,
  Button,
  KeyValue,
  Menu,
  MoneyStrip,
  Panel,
  StatusPill,
  TabPanel,
  Tabs,
  Tag,
  useToast,
} from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { AddProductModal } from './AddProduct';
import { NumberModal } from './ProductNumber';
import { BillDialog, RecordPaymentModal, useBills } from './Bills';
import { ActionDialog, BalanceActions } from './PrepaidHistory';
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
import { SubscriberOrders } from './SubscriberOrders';
import './screens.css';

// Screen 2. Everything about one billing account. Products change only through TMF622
// orders: Add product posts an add item, Deactivate a delete item; both reload the card.
// Bills: Record payment (TMF676) and a bill's hold/release (TMF678 PATCH) reload it too.

const PRODUCT_COLUMNS: Column[] = [
  { key: 'offering', label: 'Product', width: 'minmax(0, 1fr)' },
  { key: 'number', label: 'Number', width: '72px', mono: true },
  { key: 'status', label: 'Status', width: '100px' },
  { key: 'activated', label: 'Since', width: '92px', mono: true },
  { key: 'price', label: 'Price', width: '140px', align: 'right', mono: true },
  { key: 'actions', label: 'Actions', width: '40px', kind: 'actions' },
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
  // Accepted transfers and prepaid orders are followed independently of card refreshes.
  const [watches, setWatches] = useState<Watched[]>([]);
  const reload = useCallback(() => writeLock.afterWrites(() => setVersion((v) => v + 1)), []);
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
  const account = useResource(
    `account:${accountId}:${version}`,
    (signal) => loadAccount(accountId, signal),
    true,
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
      {account.refreshError && (
        <ErrorBlock
          title="Failed to refresh the billing account"
          error={account.refreshError}
          onRetry={account.retry}
        />
      )}
      {account.status === 'ok' && (
        <Card
          account={account.data}
          version={version}
          reload={reload}
          transferWatched={watches.some((w) => w.kind === 'transfer')}
          onWatch={follow}
          progress={(onTopUp) =>
            watches.length > 0 ? (
              <div data-ui="ui.subscriber.inProgress">
                <Panel title="In progress">
                  {watches.map((w) =>
                    w.kind === 'transfer' ? (
                      <TransferWatch
                        key={w.id}
                        watched={w}
                        accountId={accountId}
                        onSettled={(message) => settled(w.id, message)}
                        onDismiss={() => dismiss(w.id)}
                      />
                    ) : (
                      <OrderWatch
                        key={w.id}
                        watched={w}
                        accountId={accountId}
                        onTopUp={onTopUp}
                        onSettled={(message) => settled(w.id, message)}
                        onDismiss={() => dismiss(w.id)}
                      />
                    ),
                  )}
                </Panel>
              </div>
            ) : null
          }
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
  progress,
}: {
  account: Account;
  version: number;
  reload: () => void;
  transferWatched: boolean;
  onWatch: (watched: Watched) => void;
  progress: (onTopUp: (amount: string) => void) => ReactNode;
}) {
  const context = useSystemContext();
  const toast = useToast();
  const { navigate } = useRouter();
  const [productDialog, setProductDialog] = useState<{
    product: AccountProduct;
    change: ProductAction;
    immediate: boolean;
  } | null>(null);
  const [balanceAction, setBalanceAction] = useState<string | null>(null);
  const [numberProduct, setNumberProduct] = useState<string | null>(null);
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
  const balance = useResource(
    `prepaid:${account.id}:${version}`,
    (signal) =>
      account.ratingType === 'prepaid'
        ? loadPrepaidBalance(account.id, signal)
        : Promise.resolve(null),
    true,
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
  const [hash, setHash] = useState(() => window.location.hash.slice(1));
  const [paymentCount, setPaymentCount] = useState<number>();
  const [refundCount, setRefundCount] = useState<number>();
  const [failedCount, setFailedCount] = useState<number>();
  useEffect(() => {
    const changed = () => setHash(window.location.hash.slice(1));
    window.addEventListener('hashchange', changed);
    window.addEventListener('popstate', changed);
    return () => {
      window.removeEventListener('hashchange', changed);
      window.removeEventListener('popstate', changed);
    };
  }, []);
  const loadedCount = (resource: ResourceWithRetry<unknown[]>) =>
    resource.status === 'ok' ? (
      <span className="count">{resource.data.length === 20 ? '20+' : resource.data.length}</span>
    ) : undefined;
  const options = [
    { key: 'overview', label: 'Overview' },
    {
      key: 'bills',
      label: 'Bills',
      ...(bills.status === 'ok' ? { count: bills.data.length } : {}),
    },
    {
      key: 'payments',
      label: 'Payments',
      ...(paymentCount !== undefined && refundCount !== undefined
        ? { count: paymentCount + refundCount }
        : {}),
    },
    { key: 'charges', label: 'Charges', badge: loadedCount(charges) },
    { key: 'transactions', label: 'Transactions', badge: loadedCount(transactions) },
    ...(account.ratingType === 'prepaid'
      ? [
          { key: 'balance', label: 'Balance actions' },
          {
            key: 'commands',
            label: 'Commands',
            badge: failedCount ? <Badge kind="info">{failedCount}</Badge> : undefined,
          },
        ]
      : []),
    { key: 'account', label: 'Account' },
  ];
  const tab = options.some((option) => option.key === hash) ? hash : 'overview';
  const selectTab = (key: string) => {
    window.history.pushState(
      null,
      '',
      `${window.location.pathname}${window.location.search}#${key}`,
    );
    setHash(key);
  };
  const pane = (key: string, children: ReactNode) => (
    <div hidden={tab !== key}>
      <TabPanel id="subscriber" value={key}>
        {children}
      </TabPanel>
    </div>
  );
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
            Customer no. <span className="num">{shortId(account.customerId)}</span>
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
      <SubscriberMoneyStrip
        account={account}
        today={context.businessDate}
        settlement={settlement.status === 'ok' ? settlement.data : null}
        balance={balance}
        products={products}
        actions={
          account.ratingType === 'prepaid' ? (
            <>
              <Button
                variant="primary"
                onClick={() => {
                  setMoneyAmount(undefined);
                  setMoney('topup');
                }}
                disabled={balance.status !== 'ok' || balance.data === null}
              >
                Top up
              </Button>
              <Button
                onClick={() => {
                  setMoneyAmount(undefined);
                  setMoney('adjust');
                }}
                disabled={balance.status !== 'ok' || balance.data === null}
              >
                Adjust
              </Button>
              <Button
                onClick={() => {
                  setMoneyAmount(undefined);
                  setMoney('hold');
                }}
                disabled={balance.status !== 'ok' || balance.data === null}
              >
                Hold
              </Button>
              <Button
                onClick={() => setTransferring(true)}
                disabled={balance.status !== 'ok' || balance.data === null || transferWatched}
              >
                Transfer
              </Button>
              <Button onClick={() => setPaying(true)} disabled={bills.status !== 'ok'}>
                Record payment
              </Button>
            </>
          ) : (
            <Button
              variant="primary"
              onClick={() => setPaying(true)}
              disabled={bills.status !== 'ok'}
            >
              Record payment
            </Button>
          )
        }
      />
      <div data-ui="ui.subscriber.tabs">
        <Tabs
          id="subscriber"
          label="Subscriber sections"
          options={options}
          value={tab}
          onChange={selectTab}
        />
      </div>
      {pane(
        'account',
        <>
          {' '}
          <AccountSummary
            account={account}
            today={context.businessDate}
            settlement={settlement.status === 'ok' ? settlement.data : null}
            onChanged={reload}
          />
        </>,
      )}
      {pane(
        'overview',
        <div className="subscriber-overview">
          <div className="subscriber-overview-main">
            <SubscriberOrders
              account={account}
              version={version}
              onChanged={reload}
              onWatch={(id, label) => onWatch(watch('order', id, label))}
            />
            <Panel
              title="Active products"
              right={
                products.status === 'ok' ? (
                  <span className="bss-panel-count">{products.data.length}</span>
                ) : undefined
              }
            >
              <TableHead
                columns={PRODUCT_COLUMNS.map((column) =>
                  column.key === 'price'
                    ? { ...column, label: `Price ${account.currency}` }
                    : column,
                )}
                density="compact"
              />
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
                      onNumber={() => setNumberProduct(product.id)}
                      onPick={(change, immediate) =>
                        setProductDialog({ product, change, immediate })
                      }
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
            {progress((amount) => {
              setMoneyAmount(amount);
              setMoney('topup');
            })}
          </div>
          <aside
            className="subscriber-overview-rail"
            data-ui="ui.subscriber.activity"
            aria-labelledby="subscriber-activity-title"
          >
            <div className="subscriber-activity-heading">
              <h2 id="subscriber-activity-title">Recent activity</h2>
              <a
                className="bss-link"
                href="#transactions"
                onClick={(event) => {
                  event.preventDefault();
                  selectTab('transactions');
                }}
              >
                All transactions
              </a>
            </div>
            {transactions.status === 'loading' && (
              <p className="bss-secondary">Loading movements…</p>
            )}
            {transactions.status === 'error' && (
              <ErrorBlock
                title="Failed to load movements"
                error={transactions.error}
                onRetry={transactions.retry}
              />
            )}
            {transactions.status === 'ok' &&
              (transactions.data.length === 0 ? (
                <p className="subscriber-activity-empty">No movements yet.</p>
              ) : (
                <ol className="subscriber-activity-list">
                  {transactions.data.slice(0, 8).map((movement) => (
                    <li key={`${movement.type}:${movement.id}`}>
                      <span className="subscriber-activity-description">
                        {movement.note}{' '}
                        <span className="subscriber-activity-type">
                          {TYPE_LABEL[movement.type] ?? movement.type}
                        </span>
                      </span>
                      <span className="subscriber-activity-amount">
                        <Amount signed cents={toCents(movement.amount.value)} />
                      </span>
                      <span className="subscriber-activity-date">{movement.date}</span>
                      <span className="subscriber-activity-after">
                        <Amount cents={toCents(movement.balanceAfter.value)} />
                      </span>
                    </li>
                  ))}
                </ol>
              ))}
            {account.ratingType === 'prepaid' && (
              <p className="subscriber-activity-footnote">
                Ledger balance after each movement. Holds change Reserved, not Ledger.
              </p>
            )}
          </aside>
        </div>,
      )}
      {account.ratingType === 'prepaid' &&
        pane(
          'commands',
          <>
            <FailedCommands
              accountId={account.id}
              version={version}
              onRetried={reload}
              onCount={setFailedCount}
            />
            <Link className="bss-link" href={`/prepaid-commands?account=${account.id}`}>
              Prepaid commands
            </Link>
          </>,
        )}
      {pane(
        'bills',
        <>
          <Button onClick={() => setPaying(true)} disabled={bills.status !== 'ok'}>
            Record payment
          </Button>
          <AccountBills
            account={account}
            version={version}
            bills={bills}
            onOpen={setOpenBill}
            onCompleted={reload}
          />
        </>,
      )}
      {pane(
        'payments',
        <>
          <PaymentsPanel
            accountId={account.id}
            version={version}
            onCount={setPaymentCount}
            onChanged={reload}
            refundAction={
              <RefundAction
                account={account}
                settlement={settlement.status === 'ok' ? settlement.data : null}
                onChanged={reload}
                label="Record refund"
                ui="ui.subscriber.payments.recordRefund"
              />
            }
          />
          <RefundsPanel accountId={account.id} version={version} onCount={setRefundCount} />
        </>,
      )}
      {account.ratingType === 'prepaid' &&
        pane(
          'balance',
          <BalanceActions
            balance={balance}
            accountId={account.id}
            version={version}
            onOpen={setBalanceAction}
          />,
        )}
      <History account={account} charges={charges} transactions={transactions} tab={tab} />

      {balanceAction !== null && (
        <ActionDialog
          id={balanceAction}
          load={(signal) => loadBalanceAction(balanceAction, account.id, signal)}
          onClose={() => setBalanceAction(null)}
          onChanged={() => {
            setBalanceAction(null);
            reload();
          }}
        />
      )}
      {numberProduct !== null && (
        <NumberModal
          accountId={account.id}
          productId={numberProduct}
          onClose={() => setNumberProduct(null)}
          onDone={(message, accepted) => {
            toast(message);
            if (accepted?.id) onWatch(watch('order', accepted.id, accepted.label));
            reload();
          }}
        />
      )}
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

      {productDialog !== null && productDialog.change === 'offering' && (
        <ChangeOfferingDialog
          account={account}
          product={productDialog.product}
          onClose={() => setProductDialog(null)}
          onRefresh={reload}
          onDone={(message, accepted) => {
            setProductDialog(null);
            toast(message);
            if (accepted?.id) onWatch(watch('order', accepted.id, accepted.label));
            reload();
          }}
        />
      )}
      {productDialog !== null && productDialog.change !== 'offering' && (
        <ProductChangeDialog
          onTopUp={(amount) => {
            setProductDialog(null);
            setMoneyAmount(amount);
            setMoney('topup');
          }}
          immediateEnd={productDialog.immediate}
          change={productDialog.change}
          account={account}
          product={productDialog.product}
          onClose={() => setProductDialog(null)}
          onRefresh={reload}
          onDone={(message, accepted) => {
            setProductDialog(null);
            toast(message);
            if (accepted?.id) onWatch(watch('order', accepted.id, accepted.label));
            reload();
          }}
        />
      )}
      {adding && (
        <AddProductModal
          account={account}
          activeOfferingIds={activeOfferings}
          onClose={() => setAdding(false)}
          onDraftSaved={() => {
            setAdding(false);
            reload();
          }}
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

function DueOnBills({ settlement }: { settlement: AccountSettlement | null }) {
  return (
    <>{settlement === null ? '…' : <Amount cents={toCents(settlement.outstanding.value)} />}</>
  );
}

function DueCaption({ settlement }: { settlement: AccountSettlement | null }) {
  return settlement !== null && settlement.overdue.count > 0 ? (
    <span className="subscriber-overdue">
      {settlement.overdue.count} overdue · {formatCents(toCents(settlement.overdue.amount.value))}
    </span>
  ) : settlement !== null && settlement.onHold.count > 0 ? (
    <>{settlement.onHold.count} on hold</>
  ) : null;
}

function AccountSummary({
  account,
  today,
  settlement,
  onChanged,
}: {
  account: Account;
  today: string;
  settlement: AccountSettlement | null;
  onChanged: () => void;
}) {
  const available =
    account.creditLimitCents === null ? null : account.balanceCents + account.creditLimitCents;
  const items: [ReactNode, ReactNode][] = [
    ['Currency', account.currency],
    ['Billing cycle day', `Day ${account.cycleDay}`],
    ['Next cycle', nextCycleDate(today, account.cycleDay)],
    ...(account.ratingType === 'postpaid'
      ? ([
          [
            'Credit limit',
            account.creditLimitCents === null ? '—' : <Amount cents={account.creditLimitCents} />,
          ],
          ['Available incl. credit', available === null ? '—' : <Amount cents={available} />],
        ] as [ReactNode, ReactNode][])
      : []),
    [
      'Due on bills',
      <>
        <DueOnBills settlement={settlement} />
        <div>
          <DueCaption settlement={settlement} />
        </div>
      </>,
    ],
    [
      'Unused credit',
      <>
        {settlement === null ? '…' : <Amount cents={toCents(settlement.unallocatedCredit.value)} />}{' '}
        <RefundAction account={account} settlement={settlement} onChanged={onChanged} />
      </>,
    ],
    ...(account.financialAccount === null
      ? []
      : ([
          [
            'Financial account',
            <Link className="bss-link" href={hrefOf({ name: 'financialAccounts' })}>
              {account.financialAccount.name}
            </Link>,
          ],
        ] as [ReactNode, ReactNode][])),
  ];
  return (
    <section className="subscriber-account" data-ui="ui.subscriber.account">
      <h2>Account</h2>
      <KeyValue items={items} />
    </section>
  );
}

const CAUSE_LABEL: Record<string, string> = {
  funding: 'funding ran out',
  manual: 'suspended by the operator',
  account: 'the account is not active',
};

function SubscriberMoneyStrip({
  account,
  today,
  settlement,
  balance,
  products,
  actions,
}: {
  account: Account;
  today: string;
  settlement: AccountSettlement | null;
  balance: ResourceWithRetry<PrepaidBalance | null>;
  products: ResourceWithRetry<AccountProduct[]>;
  actions: ReactNode;
}) {
  const figures = balance.status === 'ok' ? balance.data : null;
  const rows = products.status === 'ok' ? products.data : null;
  const dates = (rows ?? []).flatMap((p) => (p.paidThrough ? [p.paidThrough] : [])).sort();
  const paid = dates[0];
  const days = paid ? Math.round((Date.parse(paid) - Date.parse(today)) / 86400000) : null;
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
  const value = (cents: number | undefined) =>
    balance.status === 'error' ? '—' : cents === undefined ? '…' : <Amount cents={cents} />;
  const over =
    account.creditLimitCents !== null && account.balanceCents < -account.creditLimitCents
      ? -account.balanceCents - account.creditLimitCents
      : 0;
  return (
    <div className="subscriber-money" data-ui="ui.subscriber.moneyStrip">
      <MoneyStrip
        actions={actions}
        operators={account.ratingType === 'prepaid' ? ['−', '='] : []}
        divider={account.ratingType === 'prepaid' ? [3] : [1]}
        figures={
          account.ratingType === 'prepaid'
            ? [
                {
                  label: 'Ledger',
                  value: <Amount cents={account.balanceCents} />,
                  emphasis: 'figure',
                  sub: account.balanceCents < 0 ? 'Below zero' : undefined,
                },
                {
                  label: 'Reserved',
                  ui: 'ui.subscriber.prepaid.reserved',
                  value: value(figures?.reservedCents),
                  emphasis: 'figure',
                  sub: 'holds not spent yet',
                },
                {
                  label: 'Available',
                  ui: 'ui.subscriber.prepaid.available',
                  value: value(figures?.availableCents),
                  emphasis: 'answer',
                  sub: 'ledger less holds',
                },
                {
                  label: 'Paid through',
                  ui: 'ui.subscriber.prepaid.paidThrough',
                  value: rows === null ? '…' : (paid ?? '—'),
                  sub: (
                    <>
                      {days === null ? null : days >= 0 ? `in ${days} days` : `${-days} days ago`}
                      <div>earliest of its products, service ends here</div>
                    </>
                  ),
                },
                {
                  label: 'Shortfall',
                  ui: 'ui.subscriber.prepaid.shortfall',
                  value: (
                    <>
                      {rows === null ? (
                        '…'
                      ) : worst?.needs_topup ? (
                        <Amount cents={toCents(worst.needs_topup.shortfall)} />
                      ) : (
                        '—'
                      )}
                    </>
                  ),
                  tone: worst ? 'negative' : 'muted',
                  sub: (
                    <>
                      {worst?.needs_topup ? (
                        <>
                          renewal needs {formatCents(toCents(worst.needs_topup.required))},
                          available {formatCents(toCents(worst.needs_topup.available))}
                          {short.length > 1 ? ` · ${short.length} products` : ''}
                        </>
                      ) : rows === null ? null : (
                        'none'
                      )}
                      {causes.length > 0 && (
                        <div>Suspension: {causes.map((c) => CAUSE_LABEL[c] ?? c).join(', ')}</div>
                      )}
                      <div>
                        <Link
                          className="bss-link"
                          data-ui="ui.subscriber.prepaid.commands"
                          href={`/prepaid-commands?account=${account.id}`}
                        >
                          Commands
                        </Link>
                      </div>
                    </>
                  ),
                },
              ]
            : [
                {
                  label: 'Balance',
                  value: <Amount cents={account.balanceCents} />,
                  emphasis: 'answer',
                  sub: account.balanceCents < 0 ? 'In arrears' : undefined,
                },
                {
                  label: 'Credit limit',
                  value:
                    account.creditLimitCents === null ? (
                      '—'
                    ) : (
                      <Amount cents={account.creditLimitCents} />
                    ),
                  sub:
                    over > 0 ? (
                      <span className="subscriber-over-limit">Over by {formatCents(over)}</span>
                    ) : undefined,
                },
                {
                  label: 'Cycle day',
                  value: `Day ${account.cycleDay}`,
                  sub: `next bill ${nextCycleDate(today, account.cycleDay)}`,
                },
                {
                  label: 'Due on bills',
                  value: <DueOnBills settlement={settlement} />,
                  sub: <DueCaption settlement={settlement} />,
                },
              ]
        }
      />
      {account.ratingType === 'prepaid' && balance.status === 'error' && (
        <Note tone="negative">Failed to load the prepaid balance ({balance.error.message})</Note>
      )}
    </div>
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
  onTopUp,
  onPick,
  onNumber,
}: {
  account: Account;
  product: AccountProduct;
  onDone: (message: string, accepted?: PendingOrder) => void;
  onTopUp: (amount: string) => void;
  onPick: (change: ProductAction, immediate: boolean) => void;
  onNumber: () => void;
}) {
  const context = useSystemContext();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const note = statusNote(product);
  const ending = product.status === 'pendingTerminate';
  const name = product.productOffering.name;

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
      onDone(
        `${name}: the ${product.undoableStatusChange ? 'status' : 'pending'} change was cancelled`,
      );
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
        density="compact"
        cells={{
          offering: (
            <div>
              <span className="bss-name">{name}</span>
              <div className="bss-code">{code}</div>
              {fundingWait !== null && (
                <div className="bss-secondary">
                  Waiting for funds: short {formatCents(toCents(fundingWait.shortfall))}
                </div>
              )}
              {problem !== null && <Note tone="negative">{problem}</Note>}
            </div>
          ),
          number: <span className="bss-dash">—</span>,
          status: (
            <StatusPill
              kind="product"
              status={product.status}
              {...(note ? { label: note.label } : {})}
            />
          ),
          activated: (
            <>
              <span>
                {product.startDate > context.businessDate
                  ? `Starts ${product.startDate}`
                  : product.startDate}
              </span>
              {product.nextChargeDate !== null && (
                <span className="subscriber-product-next">
                  Next charge <span>{product.nextChargeDate}</span>
                </span>
              )}
            </>
          ),
          price:
            product.price === null ? (
              <span className="bss-dash">—</span>
            ) : (
              <>
                <Amount cents={toCents(product.price.value)} currency={product.price.unit} />{' '}
                <span className="bss-secondary">/ {product.recurringChargePeriod ?? 'mo'}</span>
              </>
            ),
          actions: !ending ? (
            <ProductActionsMenu
              undoable={product.undoableStatusChange}
              busy={busy}
              onUndo={() => {
                if (product.undoableStatusChange)
                  void undoChange(product.undoableStatusChange.productOrder.id);
              }}
              items={offeredChanges(product, account.ratingType)}
              onPick={(change) => onPick(change, false)}
              extra={[
                { label: 'Subscriber number', onClick: onNumber },
                ...(fundingWait !== null
                  ? [
                      {
                        label: 'Top up',
                        ui: 'ui.subscriber.topUpProduct',
                        onClick: () =>
                          onTopUp(formatCents(toCents(fundingWait.shortfall)).replace(/,/g, '')),
                      },
                    ]
                  : []),
                {
                  label: 'Deactivate',
                  tone: 'negative',
                  ui: 'ui.subscriber.deactivate',
                  onClick: () => onPick('end', true),
                },
              ]}
            />
          ) : (
            <Menu
              label="Subscriber number"
              items={[{ label: 'Subscriber number', onClick: onNumber }]}
            />
          ),
        }}
      />
      {ending && (
        <div className="subscriber-pending-change" role="row">
          <span className="subscriber-pending-label">Pending change</span>
          <span>Ends on {product.terminationDate}</span>
          <Button
            small
            disabled={busy}
            data-ui="ui.subscriber.undoDeactivation"
            onClick={() => void undo()}
          >
            Undo deactivation
          </Button>
        </div>
      )}
      {product.pendingChanges.map((c) => (
        <div
          key={c.productOrder.id}
          className="subscriber-pending-change"
          role="row"
          data-ui="ui.subscriber.pendingChange"
        >
          <span className="subscriber-pending-label">Pending change</span>
          <span>
            {c.kind === 'status'
              ? c.status === 'suspended'
                ? `Suspends on ${c.date}`
                : `Resumes on ${c.date}`
              : `Changes to ${c.productOffering?.name ?? 'another offering'} on ${c.date}${
                  account.ratingType === 'prepaid' && c.date < context.businessDate
                    ? ' (waits for funds)'
                    : ''
                }`}
          </span>{' '}
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
    </div>
  );
}

function History({
  account,
  charges,
  transactions,
  tab,
}: {
  tab: string;
  account: Account;
  charges: ResourceWithRetry<Charge[]>;
  transactions: ResourceWithRetry<Transaction[]>;
}) {
  return (
    <>
      <div hidden={tab !== 'charges'}>
        <TabPanel id="subscriber" value="charges">
          <section data-ui="ui.subscriber.charges">
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
                      <div className="bss-secondary">
                        {c.productOffering?.name ?? 'Transfer fee'}
                      </div>
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
          </section>
        </TabPanel>
      </div>
      <div hidden={tab !== 'transactions'}>
        <TabPanel id="subscriber" value="transactions">
          <section data-ui="ui.subscriber.transactions">
            <Panel title="Transaction history" subtitle={account.currency}>
              <TableHead columns={TX_COLUMNS} />
              <SimpleList
                resource={transactions}
                columns={TX_COLUMNS}
                errorTitle="Failed to load transactions"
                empty={{
                  title: 'No transactions',
                  text: 'No payments or charges on this account.',
                }}
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
          </section>
        </TabPanel>
      </div>
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
