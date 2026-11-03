import { submitDialogPrimary } from '../ui/kit';
import { useEffect, useState, type ReactNode } from 'react';
import {
  loadPayment,
  loadPayments,
  loadRefundHistory,
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
import { accountLabel, shortId } from '../format/ids';
import { formatAmount, formatInt } from '../format/money';
import { Modal, PageTitle, Panel, StatusPill, Segmented, TextInput, Button } from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';
import { PaymentRefundModal } from './AccountActions';

// Payments and refunds history (docs/next-slices-design.md section 19): TMF676 reads only.
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

function moneyText(money: { value: number; unit: string }): string {
  return `${formatAmount(money.value)} ${money.unit}`;
}

function newestFirst<T>(rows: T[], at: (row: T) => string): T[] {
  return [...rows].sort((a, b) => at(b).localeCompare(at(a)));
}

function stamp(instant: string, timeZone: string): string {
  return `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone).slice(0, 5)}`;
}

function paymentCells(payment: PaymentRecord, timeZone: string) {
  return {
    date: stamp(payment.paymentDate, timeZone),
    amount: moneyText(payment.totalAmount),
    method: payment.paymentMethod.name ?? payment.paymentMethod['@type'] ?? '—',
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
    loadPayments({ account: accountId, status: null }, signal),
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
          total={(data) => data.length}
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
    <Modal
      onSubmit={submitDialogPrimary}
      title={`Payment ${shortId(paymentId)}`}
      width={640}
      onClose={onClose}
      ui="ui.payments.detail"
      subtitle={payment === null ? undefined : stamp(payment.paymentDate, timeZone)}
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
              <Field label="Total">{moneyText(payment.totalAmount)}</Field>
              <Field label="Net">{moneyText(payment.amount)}</Field>
              <Field label="Tax">{moneyText(payment.taxAmount)}</Field>
              <Field label="Status">
                <StatusPill kind="payment" status={payment.status} />
              </Field>
            </div>
            <dl className="bss-caption">
              <dt>Subscriber</dt>
              <dd>
                <Link
                  className="bss-link"
                  href={hrefOf({ name: 'subscriber', accountId: payment.account.id })}
                >
                  {payment.account.name ?? accountLabel(payment.account.id)}
                </Link>
              </dd>
              <dt>Method</dt>
              <dd>{payment.paymentMethod.name ?? payment.paymentMethod['@type'] ?? '—'}</dd>
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
                    Bill {shortId(item.item.id)}: {moneyText(item.amount)}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function inRange(instant: string, from: string, to: string, timeZone: string): boolean {
  const day = businessDate(instant, timeZone);
  return (from === '' || day >= from) && (to === '' || day <= to);
}

/** The global Payments screen. */
export function PaymentsScreen() {
  useSetCrumbs([{ label: 'Payments' }], 'payments');
  const { timeZone } = useSystemContext();
  const [tab, setTab] = useState<Tab>('payments');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const filter = { account, status: status === 'all' ? null : status };
  const key = `${account ?? ''}:${status}`;
  const payments = useResource(`payments-all:${key}`, (signal) => loadPayments(filter, signal));
  const refunds = useResource(`refunds-all:${key}`, (signal) => loadRefundHistory(filter, signal));

  const dropAccount = () => {
    window.history.replaceState(null, '', '/payments');
    setAccount(null);
  };
  const reset = () => {
    setStatus('all');
    setFrom('');
    setTo('');
    if (account !== null) dropAccount();
  };
  const name =
    payments.status === 'ok'
      ? (payments.data.find((p) => p.account.id === account)?.account.name ?? null)
      : null;

  const shown = (rows: number, all: number) =>
    rows === all ? `${formatInt(rows)}` : `${formatInt(rows)} of ${formatInt(all)}`;

  return (
    <div className="bss-screen" data-ui="ui.payments">
      <div className="bss-title-row">
        <PageTitle title="Payments" />
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
        <label className="bss-caption" htmlFor="payments-from">
          From
        </label>
        <TextInput
          id="payments-from"
          type="date"
          mono
          value={from}
          max={to === '' ? undefined : to}
          onChange={(event) => setFrom(event.target.value)}
        />
        <label className="bss-caption" htmlFor="payments-to">
          To
        </label>
        <TextInput
          id="payments-to"
          type="date"
          mono
          value={to}
          min={from === '' ? undefined : from}
          onChange={(event) => setTo(event.target.value)}
        />
        {account !== null && (
          <span className="bss-run-tag" data-ui="ui.payments.accountChip">
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
        {tab === 'payments' ? (
          <>
            <TableHead columns={GLOBAL_PAYMENT_COLUMNS} />
            <ListState
              resource={payments}
              columns={GLOBAL_PAYMENT_COLUMNS}
              errorTitle="Failed to load payments"
              empty={{ title: 'No payments', text: 'Received payments appear here.' }}
              total={(data) => data.length}
              rows={(data) =>
                newestFirst(
                  data.filter((p) => inRange(p.paymentDate, from, to, timeZone)),
                  (p) => p.paymentDate,
                )
              }
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
                              {payment.account.name ?? accountLabel(payment.account.id)}
                            </Link>
                          </span>
                        ),
                        ...paymentCells(payment, timeZone),
                      }}
                    />
                  ))}
                  <TableFooter left={`Showing ${shown(rows.length, data.length)}`} />
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
              total={(data) => data.length}
              rows={(data) =>
                newestFirst(
                  data.filter((r) => inRange(r.refundDate, from, to, timeZone)),
                  (r) => r.refundDate,
                )
              }
              onResetFilters={reset}
            >
              {(rows, data) => (
                <>
                  {rows.map((refund) => (
                    <RefundRow key={refund.id} refund={refund} timeZone={timeZone} />
                  ))}
                  <TableFooter left={`Showing ${shown(rows.length, data.length)}`} />
                </>
              )}
            </ListState>
          </>
        )}
      </Panel>
      {open !== null && (
        <PaymentDialog paymentId={open} onClose={() => setOpen(null)} onChanged={refunds.retry} />
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
          <Link
            className="bss-link"
            href={hrefOf({ name: 'subscriber', accountId: refund.account.id })}
          >
            {refund.account.name ?? accountLabel(refund.account.id)}
          </Link>
        ),
        date: stamp(refund.refundDate, timeZone),
        amount: moneyText(refund.totalAmount),
        method: refund.paymentMethod.name ?? '—',
        status: <StatusPill kind="payment" status={refund.status} />,
        reference,
      }}
    />
  );
}
