import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useState, type ReactNode } from 'react';
import {
  loadBillDetail,
  loadBills,
  recordPayment,
  setBillState,
  type Account,
  type Bill,
  type BillDetail,
  type BillState,
} from '../api/bss';
import { explain } from '../api/client';
import { useResource, type ResourceWithRetry } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { clockTime, businessDate, lastDay } from '../format/dates';
import { accountLabel } from '../format/ids';
import { formatCents, parseAmount, positiveAmountError, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Button, Dialog, Field, MoneyField, Modal, Panel, StatusPill, TextInput } from '../ui/kit';
import { Id } from '../ui/Id';
import { Footer } from './FinancialForms';
import { uncertain } from './submissionKey';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { BillLinks } from './BillLinks';

// The bill lifecycle on the subscriber card (docs/archive/bill-lifecycle-design.md 6.7): the
// account's bills, a bill dialog with hold/release, and Record payment. Settlement is the
// backend's: the UI shows what it reports and never allocates anything itself.

const BILL_COLUMNS: Column[] = [
  { key: 'no', label: 'Bill', width: '120px', mono: true },
  { key: 'date', label: 'Bill date', width: '100px', mono: true },
  { key: 'due', label: 'Due', width: '1fr', mono: true },
  { key: 'amount', label: 'Total', width: '120px', align: 'right', mono: true },
  { key: 'remaining', label: 'Remaining', width: '120px', align: 'right', mono: true },
  { key: 'state', label: 'State', width: '120px' },
];

/** Integer cents of a canonical amount string ("1290.50" -> 129050). */
function centsOf(canonical: string): number {
  return Number(canonical.replace('.', ''));
}

export function isOverdue(bill: Bill, today: string): boolean {
  return bill.remainingCents > 0 && bill.dueDate < today;
}

export function useBills(account: Account, version: number): ResourceWithRetry<Bill[]> {
  const context = useSystemContext();
  return useResource(
    `bills:${account.id}:${version}`,
    (signal) => loadBills(account.id, context.timeZone, signal),
    true,
  );
}

export function BillsPanel({
  bills,
  onOpen,
  action,
}: {
  bills: ResourceWithRetry<Bill[]>;
  onOpen: (bill: Bill) => void;
  action?: ReactNode;
}) {
  const context = useSystemContext();
  return (
    <div className="bss-screen" data-ui="ui.subscriber.bills">
      <Panel
        title="Bills"
        subtitle="CustomerBill"
        right={
          <>
            {bills.status === 'ok' && <span className="bss-panel-count">{bills.data.length}</span>}{' '}
            {action}
          </>
        }
      >
        <TableHead columns={BILL_COLUMNS} />
        <ListState
          resource={bills}
          columns={BILL_COLUMNS}
          errorTitle="Failed to load bills"
          empty={{ title: 'No bills', text: 'A bill is issued on the billing cycle day.' }}
          total={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {}}
        >
          {(rows) =>
            rows.map((bill) => (
              <TableRow
                key={bill.id}
                columns={BILL_COLUMNS}
                onClick={() => onOpen(bill)}
                cells={{
                  no: bill.billNo,
                  date: bill.billDate,
                  due: bill.creditNote ? (
                    <span className="bss-secondary">credit note</span>
                  ) : (
                    <span>
                      {bill.dueDate}
                      {isOverdue(bill, context.businessDate) && (
                        <span className="caption negative"> overdue</span>
                      )}
                    </span>
                  ),
                  amount: <Amount cents={bill.amountDueCents} />,
                  remaining: <Amount cents={bill.remainingCents} />,
                  state: <StatusPill kind="bill" status={bill.state} />,
                }}
              />
            ))
          }
        </ListState>
      </Panel>
    </div>
  );
}

const HOLD_LABEL: Record<string, string> = {
  onHold: 'Put on hold',
  sent: 'Release',
  partiallyPaid: 'Release',
};

const CAUSE_LABEL: Record<string, string> = {
  run: 'billing run',
  payment: 'payment',
  operator: 'operator',
};

export function BillDialog({
  billId,
  cycleId,
  onClose,
  onChanged,
}: {
  billId: string;
  cycleId?: string | undefined;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const context = useSystemContext();
  const detail = useResource(`bill:${billId}`, (signal) => loadBillDetail(billId, signal));
  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title={detail.status === 'ok' ? `Bill ${detail.data.billNo}` : 'Bill'}
      width={760}
      onClose={onClose}
      ui="ui.subscriber.billDetail"
      subtitle={
        detail.status === 'ok'
          ? `${accountLabel(detail.data.billingAccount.id)} · billed ${detail.data.billDate}`
          : undefined
      }
    >
      <div className="bss-modal-body">
        {detail.status === 'loading' && <SkeletonRows columns={BILL_COLUMNS} rows={4} />}
        {detail.status === 'error' && (
          <ErrorBlock title="Failed to load the bill" error={detail.error} onRetry={detail.retry} />
        )}
        {detail.status === 'ok' && (
          <>
            <BillBody bill={detail.data} timeZone={context.timeZone} onChanged={onChanged} />
            <BillLinks billId={billId} cycleId={cycleId} />
          </>
        )}
      </div>
    </Modal>
  );
}

function BillBody({
  bill,
  timeZone,
  onChanged,
}: {
  bill: BillDetail;
  timeZone: string;
  onChanged: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState<BillState | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const when = (instant: string) =>
    `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone).slice(0, 5)}`;

  async function change(state: BillState) {
    setBusy(true);
    setProblem(null);
    try {
      await setBillState(bill.id, state);
      setConfirming(null);
      onChanged(
        state === 'onHold' ? `Bill ${bill.billNo} put on hold` : `Bill ${bill.billNo} released`,
      );
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="bss-kpis">
        <div className="bss-kpi">
          <div className="label">State</div>
          <div className="value">
            <StatusPill kind="bill" status={bill.state} />
          </div>
          {bill.overdue && <div className="caption negative">Overdue</div>}
        </div>
        <div className="bss-kpi">
          <div className="label">Period</div>
          <div className="value num">
            {bill.period.start} – {lastDay(bill.period.end)}
          </div>
        </div>
        <div className="bss-kpi">
          <div className="label">Due</div>
          <div className="value num">
            {bill.category === 'creditNote' ? '—' : bill.paymentDueDate}
          </div>
        </div>
        <div className="bss-kpi">
          <div className="label">Total</div>
          <div className="value strong">{formatCents(toCents(bill.amountDue.value))}</div>
        </div>
        <div className="bss-kpi">
          <div className="label">Remaining</div>
          <div className="value strong">{formatCents(toCents(bill.remainingAmount.value))}</div>
        </div>
      </div>

      <section>
        <div className="bss-step-head">Charges</div>
        {bill.charges.map((c) => (
          <div key={c.id} className="bss-line">
            <span>
              {c.productOffering?.name ?? 'Transfer fee'}{' '}
              <span className="bss-secondary num">
                {c.period.start} – {lastDay(c.period.end)}
              </span>
            </span>
            <Amount cents={toCents(c.amount.value)} />
          </div>
        ))}
      </section>

      <section>
        <div className="bss-step-head">Paid by</div>
        {bill.sources.length === 0 && <div className="bss-secondary">Nothing paid yet.</div>}
        {bill.sources.map((s, i) => (
          <div key={`${s.id}:${i}`} className="bss-line">
            <span>
              {s.kind === 'payment' ? `Payment · ${s.label}` : `Credit note ${s.label}`}{' '}
              <span className="bss-secondary">
                {s.allocation} · {when(s.allocatedAt)}
              </span>
            </span>
            <Amount cents={toCents(s.amount.value)} />
          </div>
        ))}
      </section>

      <section>
        <div className="bss-step-head">History</div>
        {bill.history.map((h, i) => (
          <div key={i} className="bss-line">
            <span>
              {h.fromState === null ? 'issued as new' : `${h.fromState} → ${h.toState}`}{' '}
              <span className="bss-secondary">
                {CAUSE_LABEL[h.cause] ?? h.cause}
                {h.operator !== null && ` (${h.operator})`}
              </span>
            </span>
            <span className="bss-secondary num">{when(h.changedAt)}</span>
          </div>
        ))}
      </section>

      {bill.allowedStates.length > 0 && (
        <div className="bss-bill-actions">
          {problem !== null && <Note tone="negative">{problem}</Note>}
          {bill.allowedStates.map((state) =>
            confirming === state ? (
              <span key={state} className="bss-confirm">
                <span className="ask">{HOLD_LABEL[state] ?? state}?</span>
                <Button variant="danger" small disabled={busy} onClick={() => void change(state)}>
                  Yes
                </Button>
                <Button small disabled={busy} onClick={() => setConfirming(null)}>
                  No
                </Button>
              </span>
            ) : (
              <Button
                key={state}
                variant={state === 'onHold' ? 'quiet' : 'secondary'}
                {...(state === 'onHold' ? { tone: 'negative' as const } : {})}
                data-ui="ui.subscriber.billHold"
                onClick={() => setConfirming(state)}
              >
                {HOLD_LABEL[state] ?? state}
              </Button>
            ),
          )}
        </div>
      )}
    </>
  );
}

const METHODS = ['bankTransfer', 'card', 'cash'];

export function RecordPaymentModal({
  account,
  bills,
  onClose,
  onRecorded,
}: {
  account: Account;
  bills: Bill[];
  onClose: () => void;
  onRecorded: (message: string) => void;
}) {
  const [amountText, setAmountText] = useState('');
  const [method, setMethod] = useState(METHODS[0] ?? 'cash');
  const [other, setOther] = useState('');
  const [lettering, setLettering] = useState(false);
  const [correlatorId, setCorrelatorId] = useState('');
  const [description, setDescription] = useState('');
  const [items, setItems] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);

  const open = bills.filter(
    (b) => b.remainingCents > 0 && (b.state === 'sent' || b.state === 'partiallyPaid'),
  );
  const oldestFirst = [...open].sort((a, b) => a.billDate.localeCompare(b.billDate));
  const amount = parseAmount(amountText);
  const methodName = method === 'other' ? other.trim() : method;

  const chosen = lettering
    ? oldestFirst
        .map((b) => ({ bill: b, text: items[b.id] ?? '' }))
        .filter((x) => x.text.trim() !== '')
        .map((x) => ({ bill: x.bill, amount: parseAmount(x.text) }))
    : [];
  const itemError = chosen.find(
    (x) =>
      x.amount === null || centsOf(x.amount) === 0 || centsOf(x.amount) > x.bill.remainingCents,
  );
  const lettered = chosen.reduce((sum, x) => sum + (x.amount === null ? 0 : centsOf(x.amount)), 0);
  const tooMuch = amount !== null && lettered > centsOf(amount);
  const valid =
    amount !== null && centsOf(amount) > 0 && methodName !== '' && !itemError && !tooMuch;

  function prefill(total: string | null) {
    // oldest first, up to the payment - the operator can change any line
    const next: Record<string, string> = {};
    let left = total === null ? 0 : centsOf(total);
    for (const b of oldestFirst) {
      const take = Math.min(left, b.remainingCents);
      if (take > 0) next[b.id] = formatCents(take).replace(/,/g, '');
      left -= take;
    }
    setItems(next);
  }

  async function submit() {
    if (!valid || amount === null || busy || unsure) return;
    setBusy(true);
    setProblem(null);
    try {
      const paid = await recordPayment(account.id, {
        amount,
        currency: account.currency,
        method: methodName,
        correlatorId,
        description,
        items: chosen.map((x) => ({ billId: x.bill.id, amount: x.amount ?? '0.00' })),
      });
      onRecorded(
        `Payment of ${formatCents(centsOf(amount))} ${account.currency} recorded · ` +
          (paid === 0 ? 'kept as credit' : `applied to ${paid} bill${paid === 1 ? '' : 's'}`),
      );
    } catch (error) {
      setProblem(explain(error));
      setUnsure(uncertain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Record payment"
      size="M"
      onClose={() => {
        if (!busy) onClose();
      }}
      ui="ui.subscriber.recordPayment"
      context={
        <>
          {account.customerName} · <Id id={account.id} prefix="BA-" />
        </>
      }
      footer={
        <Footer
          write={{ busy, problem, unsure, failedCall: problem ?? undefined }}
          onRefresh={() => onRecorded('Refreshed')}
        >
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busyLabel="Recording…"
            disabled={!valid || busy}
            onClick={() => void submit()}
          >
            {amount !== null && centsOf(amount) > 0
              ? `Record ${formatCents(centsOf(amount))} ${account.currency}`
              : 'Record payment'}
          </Button>
        </Footer>
      }
    >
      <fieldset className="bss-form bss-write-fields" disabled={busy}>
        <div className="money-balance-line" data-ui="ui.subscriber.recordPayment.balances">
          Balance <Amount cents={account.balanceCents} currency={account.currency} /> · Balance
          after{' '}
          <Amount
            cents={account.balanceCents + (amount === null ? 0 : centsOf(amount))}
            currency={account.currency}
          />
        </div>
        <section>
          <Field label="Amount received">
            <MoneyField
              currency={account.currency}
              aria-label="Amount received"
              placeholder="0.00"
              value={amountText}
              onChange={(event) => {
                setAmountText(event.target.value);
                if (lettering) prefill(parseAmount(event.target.value));
              }}
            />
          </Field>
          {positiveAmountError(amountText) && (
            <Note tone="negative">{positiveAmountError(amountText)}</Note>
          )}
        </section>
        <section>
          <Field label="Method">
            <div className="bss-seg" role="group" aria-label="Payment method">
              {[...METHODS, 'other'].map((m) => (
                <button
                  key={m}
                  type="button"
                  aria-pressed={m === method}
                  onClick={() => setMethod(m)}
                >
                  {m === 'other' ? 'Other' : m}
                </button>
              ))}
            </div>
          </Field>
          {method === 'other' && (
            <Field label="Payment method name">
              <TextInput
                width={220}
                aria-label="Payment method name"
                value={other}
                onChange={(event) => setOther(event.target.value)}
              />
            </Field>
          )}
        </section>
        <section className="money-fields">
          <div className="money-fields" data-ui="ui.subscriber.recordPayment.details">
            <Field label="External reference (optional)">
              <TextInput
                aria-label="External reference"
                value={correlatorId}
                onChange={(event) => setCorrelatorId(event.target.value)}
              />
            </Field>
            <Field label="Description (optional)">
              <TextInput
                aria-label="Payment description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </Field>
          </div>
          <label className="bss-check">
            <input
              type="checkbox"
              checked={lettering}
              disabled={open.length === 0}
              onChange={(event) => {
                setLettering(event.target.checked);
                if (event.target.checked) prefill(amount);
              }}
            />{' '}
            Apply to specific bills
          </label>
          <div className="caption">
            {open.length === 0
              ? 'No open bill: the payment is kept as credit for the next bills.'
              : lettering
                ? 'The rest goes to the oldest open bills.'
                : 'Paid to the oldest open bills first; anything left is kept as credit.'}
          </div>
          {lettering &&
            oldestFirst.map((b) => (
              <div key={b.id} className="bss-line">
                <span>
                  {b.billNo}{' '}
                  <span className="bss-secondary">
                    {b.billDate} · remaining {formatCents(b.remainingCents)}
                  </span>
                </span>
                <Field label={`Amount for ${b.billNo}`}>
                  <MoneyField
                    currency={account.currency}
                    aria-label={`Amount for ${b.billNo}`}
                    value={items[b.id] ?? ''}
                    onChange={(event) => setItems({ ...items, [b.id]: event.target.value })}
                  />
                </Field>
              </div>
            ))}
          {itemError && (
            <Note tone="negative">
              {itemError.bill.billNo}: enter at most its remaining amount.
            </Note>
          )}
          {tooMuch && <Note tone="negative">The bills add up to more than the payment.</Note>}
        </section>
      </fieldset>
    </Dialog>
  );
}
