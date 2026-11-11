import { submitDialogPrimary } from '../ui/kit';
import { useEffect, useState, type ReactNode } from 'react';
import {
  loadPayment,
  loadPaymentPage,
  loadPayments,
  loadRefundPage,
  PAYMENT_PAGE,
  PAYMENT_STATUSES,
  referenceOf,
  type PaymentRecord,
  type PaymentStatus,
  type RefundRecord,
} from '../api/payments';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate, clockTime } from '../format/dates';
import { shortId } from '../format/ids';
import { toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import { paymentMethodLabel } from '../format/labels';
import { Coded, Dialog, PageHeader, Panel, StatusPill, Segmented, Button } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { PageFooter, usePageOffset } from '../ui/paging';
import './screens.css';
import { PaymentRefundModal } from './AccountActions';

// Payments and refunds history (docs/ui.md): TMF676 reads only.
// The card panel lists one account's payments with a detail dialog; the global screen has
// the Payments and Refunds tabs with a status filter, an account chip (?account=) and a
// date range applied client side to the loaded rows.

const PAYMENT_COLUMNS: Column[] = [
  { key: 'date', label: 'Date', width: '170px', mono: true },
  { key: 'amount', label: 'Amount', width: '130px', align: 'right', mono: true },
  { key: 'method', label: 'Method', width: '140px' },
  { key: 'status', label: 'Status', width: '100px' },
  { key: 'reference', label: 'Reference', width: 'minmax(0, 1fr)' },
];

const GLOBAL_PAYMENT_COLUMNS: Column[] = [
  { key: 'account', label: 'Subscriber', width: '200px' },
  ...PAYMENT_COLUMNS,
];

const REFUND_COLUMNS: Column[] = [
  { key: 'account', label: 'Subscriber', width: '200px' },
  { key: 'date', label: 'Date', width: '170px', mono: true },
  { key: 'amount', label: 'Amount', width: '130px', align: 'right', mono: true },
  { key: 'method', label: 'Method', width: '140px' },
  { key: 'status', label: 'Status', width: '100px' },
  { key: 'reference', label: 'Description / reference', width: 'minmax(0, 1fr)' },
];

type Tab = 'payments' | 'refunds';
type StatusFilter = PaymentStatus | 'all';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function accountFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get('account');
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

function money(value: { value: number; unit: string }, { signed = false, negate = false } = {}) {
  const cents = toCents(value.value);
  return <Amount cents={negate ? -cents : cents} currency={value.unit} signed={signed} />;
}

function newestFirst<T>(rows: T[], at: (row: T) => string): T[] {
  return [...rows].sort((a, b) => at(b).localeCompare(at(a)));
}

function stamp(instant: string, timeZone: string): string {
  return `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone).slice(0, 5)}`;
}

function methodCell(code: string | undefined) {
  return code === undefined ? '—' : <Coded code={code} label={paymentMethodLabel(code)} />;
}

function paymentCells(payment: PaymentRecord, timeZone: string) {
  return {
    date: stamp(payment.paymentDate, timeZone),
    amount: money(payment.totalAmount, { signed: true }),
    method: methodCell(payment.paymentMethod.name ?? payment.paymentMethod['@type']),
    status: <StatusPill kind="payment" status={payment.status} />,
    reference: referenceOf(payment),
  };
}

/** The subscriber card's Payments panel. */
export function PaymentsPanel({
  accountId,
  version,
  onCount,
  refundAction,
  onChanged,
}: {
  accountId: string;
  version: number;
  onCount?: (count: number | undefined) => void;
  refundAction?: ReactNode;
  onChanged?: (() => void) | undefined;
}) {
  const { timeZone } = useSystemContext();
  const [open, setOpen] = useState<string | null>(null);
  const payments = useResource(`payments:${accountId}:${version}`, (signal) =>
    loadPayments(accountId, signal),
  );
  const count = payments.status === 'ok' ? payments.data.length : undefined;
  useEffect(() => {
    onCount?.(count);
  }, [count, onCount]);
  return (
    <div data-ui="ui.subscriber.payments">
      <Panel
        title="Payments"
        subtitle="Payment"
        right={
          <span className="money-panel-actions">
            {refundAction}
            {payments.status === 'ok' ? (
              <span className="bss-panel-count">{payments.data.length}</span>
            ) : undefined}
          </span>
        }
      >
        <TableHead columns={PAYMENT_COLUMNS} />
        <ListState
          resource={payments}
          columns={PAYMENT_COLUMNS}
          errorTitle="Failed to load payments"
          empty={{ title: 'No payments', text: 'Received payments appear here.' }}
          count={(data) => data.length}
          rows={(data) => newestFirst(data, (p) => p.paymentDate)}
          onResetFilters={() => {}}
        >
          {(rows) =>
            rows.map((payment) => (
              <TableRow
                key={payment.id}
                columns={PAYMENT_COLUMNS}
                onClick={() => setOpen(payment.id)}
                cells={paymentCells(payment, timeZone)}
              />
            ))
          }
        </ListState>
      </Panel>
      {open !== null && (
        <PaymentDialog paymentId={open} onClose={() => setOpen(null)} onChanged={onChanged} />
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="bss-kpi">
      <div className="label">{label}</div>
      <div className="value num">{children}</div>
    </div>
  );
}

/** One payment from GET /payment/{id}: amounts, method, references and what it paid. */
export function PaymentDialog({
  paymentId,
  onClose,
  onChanged,
}: {
  paymentId: string;
  onClose: () => void;
  onChanged?: (() => void) | undefined;
}) {
  const { timeZone } = useSystemContext();
  const detail = useResource(`payment:${paymentId}`, (signal) => loadPayment(paymentId, signal));
  const payment = detail.status === 'ok' ? detail.data : null;
  const [refunding, setRefunding] = useState(false);
  if (refunding && payment !== null)
    return (
      <PaymentRefundModal
        accountId={payment.account.id}
        onClose={() => {
          setRefunding(false);
          onChanged?.();
        }}
      />
    );
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={`Payment ${shortId(paymentId)}`}
      size="L"
      onClose={onClose}
      ui="ui.payments.detail"
      context={payment === null ? undefined : stamp(payment.paymentDate, timeZone)}
      footer={
        <>
          {payment !== null && (
            <Button data-ui="ui.payments.detail.recordRefund" onClick={() => setRefunding(true)}>
              Record refund
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="bss-modal-body">
        {detail.status === 'loading' && <SkeletonRows columns={PAYMENT_COLUMNS} rows={3} />}
        {detail.status === 'error' && (
          <ErrorBlock
            title="Failed to load the payment"
            error={detail.error}
            onRetry={detail.retry}
          />
        )}
        {payment !== null && (
          <>
            <div className="bss-kpis">
              <Field label="Total">{money(payment.totalAmount, { signed: true })}</Field>
              <Field label="Net">{money(payment.amount, { signed: true })}</Field>
              <Field label="Tax">{money(payment.taxAmount)}</Field>
              <Field label="Status">
                <StatusPill kind="payment" status={payment.status} />
              </Field>
            </div>
            <dl className="bss-caption">
              <dt>Id</dt>
              <dd>
                <Id id={payment.id} prefix="PAY-" />
              </dd>
              <dt>Subscriber</dt>
              <dd>
                <Link
                  className="bss-link"
                  href={hrefOf({ name: 'subscriber', accountId: payment.account.id })}
                >
                  {payment.account.name ?? 'Account'}
                </Link>{' '}
                <Id id={payment.account.id} prefix="BA-" />
              </dd>
              <dt>Method</dt>
              <dd>{methodCell(payment.paymentMethod.name ?? payment.paymentMethod['@type'])}</dd>
              <dt>Reference</dt>
              <dd>{referenceOf(payment)}</dd>
              {payment.description !== undefined && (
                <>
                  <dt>Description</dt>
                  <dd>{payment.description}</dd>
                </>
              )}
            </dl>
            <h4>Paid bills</h4>
            {(payment.paymentItem ?? []).length === 0 ? (
              <div className="bss-caption">Not applied to a bill yet (held as credit).</div>
            ) : (
              <ul>
                {(payment.paymentItem ?? []).map((item) => (
                  <li key={item.item.id}>
                    <Id id={item.item.id} prefix="BL-" />: {money(item.amount)}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}

/** The global Payments screen. */
export function PaymentsScreen() {
  useSetCrumbs([{ label: 'Payments' }], 'payments');
  const { timeZone } = useSystemContext();
  const [tab, setTab] = useState<Tab>('payments');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  const [open, setOpen] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const filter = { account, status: status === 'all' ? null : status };
  const key = `${tab}:${account ?? ''}:${status}:${version}`;
  const [offset, setOffset] = usePageOffset(key);
  const none = { items: [], more: false, offset: 0 };
  // each tab reads only its own list, one page at a time, newest first on the server (#314)
  const payments = useResource(`payments-all:${key}:${offset}`, (signal) =>
    tab === 'payments' ? loadPaymentPage(filter, offset, signal) : Promise.resolve(none),
  );
  const refunds = useResource(`refunds-all:${key}:${offset}`, (signal) =>
    tab === 'refunds' ? loadRefundPage(filter, offset, signal) : Promise.resolve(none),
  );
  const filtered = status !== 'all' || account !== null;

  const dropAccount = () => {
    window.history.replaceState(null, '', '/payments');
    setAccount(null);
  };
  const reset = () => {
    setStatus('all');
    if (account !== null) dropAccount();
  };
  const name =
    payments.status === 'ok'
      ? (payments.data.items.find((p) => p.account.id === account)?.account.name ?? null)
      : null;

  return (
    <div className="bss-screen" data-ui="ui.payments">
      <div className="bss-title-row">
        <PageHeader title="Payments" />
        <span className="bss-caption">Received payments and recorded refunds, newest first</span>
      </div>
      <div className="bss-toolbar">
        <Segmented
          label="Kind"
          value={tab}
          onChange={setTab}
          options={[
            { key: 'payments', label: 'Payments' },
            { key: 'refunds', label: 'Refunds' },
          ]}
        />
        <Segmented
          label="Status"
          value={status}
          onChange={setStatus}
          options={[
            { key: 'all', label: 'All' },
            ...PAYMENT_STATUSES.map((s) => ({ key: s, label: s })),
          ]}
        />
        {account !== null && (
          <span className="bss-run-tag" data-ui="ui.payments.accountChip">
            Account {name ?? <Id id={account} prefix="BA-" />}{' '}
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
        {tab === 'payments' ? (
          <>
            <TableHead columns={GLOBAL_PAYMENT_COLUMNS} />
            <ListState
              resource={payments}
              columns={GLOBAL_PAYMENT_COLUMNS}
              errorTitle="Failed to load payments"
              empty={{ title: 'No payments', text: 'Received payments appear here.' }}
              // filtered on the server: an empty filtered page is "no matches"
              count={(data) => (filtered ? 1 : data.items.length)}
              rows={(data) => data.items}
              onResetFilters={reset}
            >
              {(rows, data) => (
                <>
                  {rows.map((payment) => (
                    <TableRow
                      key={payment.id}
                      columns={GLOBAL_PAYMENT_COLUMNS}
                      onClick={() => setOpen(payment.id)}
                      cells={{
                        account: (
                          <span className="ellipsis" onClick={(e) => e.stopPropagation()}>
                            <Link
                              className="bss-link"
                              href={hrefOf({ name: 'subscriber', accountId: payment.account.id })}
                            >
                              {payment.account.name ?? 'Account'}
                            </Link>{' '}
                            <Id id={payment.account.id} prefix="BA-" />
                          </span>
                        ),
                        ...paymentCells(payment, timeZone),
                      }}
                    />
                  ))}
                  <PageFooter
                    offset={data.offset}
                    shown={rows.length}
                    more={data.more}
                    size={PAYMENT_PAGE}
                    onOffset={setOffset}
                    ui="ui.payments"
                  />
                </>
              )}
            </ListState>
          </>
        ) : (
          <>
            <TableHead columns={REFUND_COLUMNS} />
            <ListState
              resource={refunds}
              columns={REFUND_COLUMNS}
              errorTitle="Failed to load refunds"
              empty={{ title: 'No recorded refunds', text: 'Completed payouts appear here.' }}
              count={(data) => (filtered ? 1 : data.items.length)}
              rows={(data) => data.items}
              onResetFilters={reset}
            >
              {(rows, data) => (
                <>
                  {rows.map((refund) => (
                    <RefundRow key={refund.id} refund={refund} timeZone={timeZone} />
                  ))}
                  <PageFooter
                    offset={data.offset}
                    shown={rows.length}
                    more={data.more}
                    size={PAYMENT_PAGE}
                    onOffset={setOffset}
                    ui="ui.payments"
                  />
                </>
              )}
            </ListState>
          </>
        )}
      </Panel>
      {open !== null && (
        <PaymentDialog
          paymentId={open}
          onClose={() => setOpen(null)}
          onChanged={() => setVersion((v) => v + 1)}
        />
      )}
    </div>
  );
}

function RefundRow({ refund, timeZone }: { refund: RefundRecord; timeZone: string }) {
  const reference = refund.description ?? referenceOf(refund);
  return (
    <TableRow
      columns={REFUND_COLUMNS}
      cells={{
        account: (
          <>
            <Link
              className="bss-link"
              href={hrefOf({ name: 'subscriber', accountId: refund.account.id })}
            >
              {refund.account.name ?? 'Account'}
            </Link>{' '}
            <Id id={refund.account.id} prefix="BA-" />
          </>
        ),
        date: stamp(refund.refundDate, timeZone),
        amount: money(refund.totalAmount, { signed: true, negate: true }),
        method: methodCell(refund.paymentMethod.name),
        status: <StatusPill kind="payment" status={refund.status} />,
        reference,
      }}
    />
  );
}
