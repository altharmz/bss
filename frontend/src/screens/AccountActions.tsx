import { submitDialogPrimary } from '../ui/kit';
import { useEffect, useRef, useState } from 'react';
import {
  loadAccount,
  loadBillDemands,
  loadRefunds,
  loadSettlement,
  recordRefund,
  requestBill,
  type Account,
  type AccountSettlement,
  type Bill,
} from '../api/bss';
import { ApiError, explain } from '../api/client';
import { useResource, type ResourceWithRetry } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { formatCents, parseAmount, positiveAmountError, toCents } from '../format/money';
import { Button, Field, Dialog, Panel, TextInput, useToast } from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { BillsPanel } from './Bills';

export function refundableCredit(account: Account, settlement: AccountSettlement | null) {
  return settlement === null
    ? 0
    : Math.max(0, Math.min(account.balanceCents, toCents(settlement.unallocatedCredit.value)));
}

const uncertain = (error: unknown) =>
  !(error instanceof ApiError) || error.status === 0 || error.status >= 500;

export function RefundAction({
  account,
  settlement,
  onChanged,
  label = 'Refund unused credit',
  ui = 'ui.subscriber.recordRefund',
}: {
  account: Account;
  settlement: AccountSettlement | null;
  onChanged: () => void;
  label?: string;
  ui?: string;
}) {
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const limit = refundableCredit(account, settlement);
  return (
    <>
      {label === 'Refund unused credit' && (
        <div className="caption">
          Refundable credit {settlement === null ? '…' : formatCents(limit)}
        </div>
      )}
      <Button small data-ui={ui} disabled={limit === 0} onClick={() => setOpen(true)}>
        {label}
      </Button>
      {open && (
        <RefundModal
          account={account}
          initialLimit={limit}
          onClose={() => {
            setOpen(false);
            onChanged();
          }}
          onRecorded={() => {
            setOpen(false);
            toast('Refund recorded');
            onChanged();
          }}
        />
      )}
    </>
  );
}

export function RefundModal({
  account,
  initialLimit,
  onClose,
  onRecorded,
}: {
  account: Account;
  initialLimit: number;
  onClose: () => void;
  onRecorded: () => void;
}) {
  const [limit, setLimit] = useState(initialLimit);
  const [amountText, setAmount] = useState(formatCents(initialLimit));
  const [method, setMethod] = useState('bankTransfer');
  const [other, setOther] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unknown, setUnknown] = useState(false);
  const saving = useRef(false);
  const amount = parseAmount(amountText);
  const [whole, fraction] = (amount ?? '0.00').split('.');
  const cents = Number(whole) * 100 + Number(fraction);
  const methodName = method === 'other' ? other.trim() : method;
  const valid =
    amount !== null &&
    cents > 0 &&
    cents <= limit &&
    methodName !== '' &&
    description.trim() !== '';

  async function submit() {
    if (!valid || amount === null || saving.current || unknown) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    try {
      await recordRefund(account, amount, methodName, description);
      onRecorded();
    } catch (error) {
      setUnknown(uncertain(error));
      setProblem(explain(error));
      if (error instanceof ApiError && error.status === 409) {
        try {
          const signal = new AbortController().signal;
          const [current, credit] = await Promise.all([
            loadAccount(account.id, signal),
            loadSettlement(account.id, signal),
          ]);
          setLimit(refundableCredit(current, credit));
        } catch {
          setUnknown(true);
        }
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Record refund"
      size="M"
      onClose={() => {
        if (!saving.current) onClose();
      }}
      footer={
        <>
          <Button disabled={busy} onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            disabled={!valid || busy || unknown}
            onClick={() => void submit()}
          >
            Record refund
          </Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <p>Record a refund you have already paid out. This does not send money.</p>
        <p>
          Refundable credit {formatCents(limit)} {account.currency}
        </p>
        <label className="bss-field">
          Amount{' '}
          <TextInput
            aria-label="Refund amount"
            value={amountText}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        {!valid && (
          <p className="bss-secondary">
            Enter a positive amount with at most two decimals, within refundable credit, and a
            method.
          </p>
        )}
        {positiveAmountError(amountText) && (
          <Note tone="negative">{positiveAmountError(amountText)}</Note>
        )}
        <label className="bss-field">
          Method{' '}
          <select
            className="bss-input"
            aria-label="Refund method"
            value={method}
            onChange={(e) => setMethod(e.target.value)}
          >
            {['bankTransfer', 'card', 'cash', 'other'].map((m) => (
              <option key={m} value={m}>
                {m === 'other' ? 'Other' : m}
              </option>
            ))}
          </select>
        </label>
        {method === 'other' && (
          <TextInput
            aria-label="Refund method name"
            value={other}
            onChange={(e) => setOther(e.target.value)}
          />
        )}
        <div data-ui="ui.subscriber.refund.description">
          <Field
            label="Description (required)"
            error={description.trim() === '' ? 'Enter a refund description.' : undefined}
          >
            <TextInput
              aria-label="Refund description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>
        </div>
        {problem && <p role="alert">{problem}</p>}
        {unknown && (
          <p role="alert">
            The outcome could not be confirmed. Close this dialog and check Recorded refunds before
            submitting again.
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** A payment opens the same account-level payout form, never a payment-specific refund. */
export function PaymentRefundModal({
  accountId,
  onClose,
}: {
  accountId: string;
  onClose: () => void;
}) {
  const data = useResource(`refund-account:${accountId}`, async (signal) => {
    const [account, settlement] = await Promise.all([
      loadAccount(accountId, signal),
      loadSettlement(accountId, signal),
    ]);
    return { account, settlement };
  });
  const toast = useToast();
  if (data.status === 'ok')
    return (
      <RefundModal
        account={data.data.account}
        initialLimit={refundableCredit(data.data.account, data.data.settlement)}
        onClose={onClose}
        onRecorded={() => {
          toast('Refund recorded');
          onClose();
        }}
      />
    );
  return (
    <Dialog title="Record refund" size="M" onClose={onClose}>
      <div className="bss-modal-body">
        {data.status === 'loading' ? (
          <p>Loading refundable credit…</p>
        ) : (
          <ErrorBlock
            title="Failed to load refundable credit"
            error={data.error}
            onRetry={data.retry}
          />
        )}
      </div>
    </Dialog>
  );
}

const REFUND_COLUMNS: Column[] = [
  { key: 'date', label: 'Date', width: '180px' },
  { key: 'amount', label: 'Amount', width: '140px', align: 'right' },
  { key: 'method', label: 'Method', width: '140px' },
  { key: 'description', label: 'Description', width: '1fr' },
];

export function RefundsPanel({
  accountId,
  version,
  onCount,
}: {
  accountId: string;
  version: number;
  onCount?: (count: number | undefined) => void;
}) {
  const { timeZone } = useSystemContext();
  const refunds = useResource(`refunds:${accountId}:${version}`, (signal) =>
    loadRefunds(accountId, signal),
  );
  const count = refunds.status === 'ok' ? refunds.data.length : undefined;
  useEffect(() => {
    onCount?.(count);
  }, [count, onCount]);
  return (
    <div data-ui="ui.subscriber.refunds">
      <Panel title="Recorded refunds">
        <TableHead columns={REFUND_COLUMNS} />
        <ListState
          resource={refunds}
          columns={REFUND_COLUMNS}
          errorTitle="Failed to load refunds"
          empty={{ title: 'No recorded refunds', text: 'Completed payouts appear here.' }}
          total={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {}}
        >
          {(rows) =>
            rows.map((r) => (
              <TableRow
                key={r.id}
                columns={REFUND_COLUMNS}
                cells={{
                  date: `${businessDate(r.refundDate, timeZone)} ${clockTime(r.refundDate, timeZone)}`,
                  amount: `${formatCents(toCents(r.totalAmount.value))} ${r.totalAmount.unit}`,
                  method: r.paymentMethod.name,
                  description: r.description ?? '—',
                }}
              />
            ))
          }
        </ListState>
      </Panel>
    </div>
  );
}

const DEMAND_COLUMNS: Column[] = [
  { key: 'date', label: 'Last update', width: '180px' },
  { key: 'state', label: 'State', width: '140px' },
  { key: 'description', label: 'Explanation', width: '1fr' },
  { key: 'bill', label: 'Bill', width: '120px' },
];
const DEMAND_LABEL: Record<string, string> = {
  inProgress: 'In progress',
  done: 'Done',
  rejected: 'Rejected',
  failed: 'Failed',
};

export function AccountBills({
  account,
  version,
  bills,
  onOpen,
  onCompleted,
}: {
  account: Account;
  version: number;
  bills: ResourceWithRetry<Bill[]>;
  onOpen: (id: string) => void;
  onCompleted: () => void;
}) {
  const { timeZone } = useSystemContext();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unknown, setUnknown] = useState(false);
  const [expired, setExpired] = useState(false);
  const [deadline, setDeadline] = useState<number | null>(null);
  const saving = useRef(false);
  const seenPending = useRef(new Set<string>());
  const demands = useResource(`demands:${account.id}:${version}`, async (signal) => {
    const data = await loadBillDemands(account.id, signal);
    if (!signal.aborted) {
      if (data.some((d) => d.state === 'inProgress'))
        setDeadline((current) => current ?? Date.now() + 180_000);
      let changed = false;
      for (const d of data) {
        if (!d.id) continue;
        if (d.state === 'inProgress') seenPending.current.add(d.id);
        else if (seenPending.current.delete(d.id)) changed = true;
      }
      if (changed) onCompleted();
    }
    return data;
  });
  const pending = demands.status === 'ok' && demands.data.some((d) => d.state === 'inProgress');
  const refresh = demands.retry;
  // The deadline is independent of individual polls and their loading transitions.
  useEffect(() => {
    if (deadline === null) return;
    const timer = setTimeout(() => setExpired(true), Math.max(0, deadline - Date.now()));
    return () => clearTimeout(timer);
  }, [deadline]);
  useEffect(() => {
    if (!pending || expired) return;
    const timer = setTimeout(refresh, 5_000);
    return () => clearTimeout(timer);
  }, [pending, expired, demands, refresh]);

  async function submit() {
    if (saving.current || unknown || demands.status !== 'ok' || pending) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    try {
      const created = await requestBill(account.id);
      if (created.id) seenPending.current.add(created.id);
      setOpen(false);
      setExpired(false);
      setDeadline(Date.now() + 180_000);
      refresh();
    } catch (error) {
      setProblem(explain(error));
      setUnknown(uncertain(error));
      refresh();
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <>
      <BillsPanel
        bills={bills}
        onOpen={(b) => onOpen(b.id)}
        action={
          <Button
            data-ui="ui.subscriber.billNow"
            disabled={busy || demands.status !== 'ok' || pending}
            onClick={() => {
              setProblem(null);
              setUnknown(false);
              setOpen(true);
            }}
          >
            Bill now
          </Button>
        }
      />
      <div data-ui="ui.subscriber.billRequests">
        <Panel
          title="Bill requests"
          right={
            <Button
              onClick={() => {
                setExpired(false);
                setDeadline(Date.now() + 180_000);
                refresh();
              }}
            >
              Refresh
            </Button>
          }
        >
          {expired && pending && <p>Processing continues. Refresh to check the request.</p>}
          <TableHead columns={DEMAND_COLUMNS} />
          <ListState
            resource={demands}
            columns={DEMAND_COLUMNS}
            errorTitle="Failed to load bill requests"
            empty={{
              title: 'No bill requests',
              text: 'Use Bill now to request a bill for unbilled charges.',
            }}
            total={(data) => data.length}
            rows={(data) => data}
            onResetFilters={() => {}}
          >
            {(rows) =>
              rows.map((d) => (
                <TableRow
                  key={d.id}
                  columns={DEMAND_COLUMNS}
                  cells={{
                    date: d.lastUpdate
                      ? `${businessDate(d.lastUpdate, timeZone)} ${clockTime(d.lastUpdate, timeZone)}`
                      : '—',
                    state: DEMAND_LABEL[d.state ?? ''] ?? d.state,
                    description: d.description ?? '—',
                    bill: d.customerBill?.id ? (
                      <Button
                        small
                        onClick={() => {
                          if (d.customerBill?.id) onOpen(d.customerBill.id);
                        }}
                      >
                        Open bill
                      </Button>
                    ) : (
                      '—'
                    ),
                  }}
                />
              ))
            }
          </ListState>
        </Panel>
      </div>
      {open && (
        <Dialog
          onSubmit={submitDialogPrimary}
          title="Bill now"
          size="M"
          onClose={() => {
            if (!saving.current) setOpen(false);
          }}
          footer={
            <>
              <Button disabled={busy} onClick={() => setOpen(false)}>
                Close
              </Button>
              <Button
                variant="primary"
                disabled={busy || unknown || demands.status !== 'ok' || pending}
                onClick={() => void submit()}
              >
                Request bill
              </Button>
            </>
          }
        >
          <div className="bss-modal-body">
            <p>
              Request a bill for unbilled charges. The request is processed after a successful
              account run from tomorrow onward. If no unbilled charges remain, it is rejected.
            </p>
            {problem && <p role="alert">{problem}</p>}
            {unknown && (
              <p role="alert">
                The outcome could not be confirmed. Close this dialog and refresh Bill requests
                before submitting again.
              </p>
            )}
          </div>
        </Dialog>
      )}
    </>
  );
}
