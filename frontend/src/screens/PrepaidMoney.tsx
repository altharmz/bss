import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useRef, useState } from 'react';
import {
  balanceActionRequest,
  submitBalanceAction,
  type Account,
  type BalanceActionKind,
  type PrepaidBalance,
} from '../api/bss';
import { explain } from '../api/client';
import { useSystemContext } from '../app/context';
import { isIsoDate } from '../format/dates';
import { formatCents, parseAmount, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import { Button, Dialog, Field, MoneyField, Segmented, TextInput } from '../ui/kit';
import { uncertain, useSubmissionKey } from './submissionKey';

// The money forms of a prepaid account: top-up, adjustment (credit or debit) and operator
// hold (docs/decisions/prepaid.md P4d-P4f, frontend.md P6b). Each submission carries one
// Idempotency-Key. After an uncertain outcome (no response, a 5xx) the same unchanged form
// is sent again with the same key, so the server answers with what it stored instead of
// doing it twice; an edited form, a refusal or a success starts a new key.

export type MoneyForm = 'topup' | 'adjust' | 'hold';

const METHODS = ['bankTransfer', 'card', 'cash'];

/** Reserve both outcome labels in the shared write-button grid while saving. */
export function MoneySubmitButton({
  busy = false,
  busyLabel,
  children,
  ...props
}: React.ComponentProps<typeof Button>) {
  return (
    <Button {...props} className="bss-write-button" busy={busy}>
      <span className="bss-write-idle-label" aria-hidden={busy}>
        {children}
      </span>
      <span className="bss-write-busy-label" aria-hidden={!busy}>
        {busyLabel}
      </span>
    </Button>
  );
}

const TITLE: Record<MoneyForm, string> = {
  topup: 'Top up',
  adjust: 'Adjust balance',
  hold: 'Hold funds',
};

export function PrepaidMoneyModal({
  form,
  account,
  balance,
  initialAmount,
  onClose,
  onDone,
}: {
  form: MoneyForm;
  account: Account;
  balance: PrepaidBalance;
  /** A decimal string typed into the amount field when the form opens (Add product's shortfall). */
  initialAmount?: string;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const context = useSystemContext();
  const [amountText, setAmountText] = useState(initialAmount ?? '');
  const [direction, setDirection] = useState<'credit' | 'debit'>('credit');
  const [method, setMethod] = useState(METHODS[0] ?? 'cash');
  const [other, setOther] = useState('');
  const [reason, setReason] = useState('');
  const [expiresOn, setExpiresOn] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [retry, setRetry] = useState(false);
  const saving = useRef(false);
  const submission = useSubmissionKey();

  const kind: BalanceActionKind = form === 'adjust' ? direction : form;
  const amount = parseAmount(amountText);
  const cents = amount === null ? 0 : toCents(Number(amount));
  const methodName = method === 'other' ? other.trim() : method;
  const spendable = Math.max(0, balance.availableCents);
  const limited = kind === 'debit' || kind === 'hold';
  const expiry = expiresOn.trim();
  const expiryShape = expiry === '' || isIsoDate(expiry);
  const expiryPast = expiry !== '' && expiryShape && expiry < context.businessDate;
  const valid =
    amount !== null &&
    cents > 0 &&
    (!limited || cents <= spendable) &&
    (form !== 'topup' || methodName !== '') &&
    (form !== 'hold' || (expiryShape && !expiryPast));

  async function submit() {
    if (!valid || amount === null || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    const request = balanceActionRequest(
      {
        kind,
        accountId: account.id,
        bucketId: balance.bucketId,
        currency: account.currency,
        amount,
        method: methodName,
        reason,
        expiresOn: form === 'hold' && expiry !== '' ? expiry : null,
      },
      context.timeZone,
    );
    const key = submission.keyFor(request.path + request.body);
    try {
      const stored = await submitBalanceAction(request, key);
      submission.settle();
      if (stored.status === 'failed') {
        // a business refusal is stored: a new submission is a new action
        setRetry(false);
        setProblem(stored.reason ?? 'The balance action was refused.');
        return;
      }
      onDone(`${TITLE[form]}: ${formatCents(cents)} ${account.currency} recorded`);
    } catch (error) {
      if (uncertain(error)) {
        setRetry(true);
        setProblem(
          `${explain(error)}. Unsure whether it was recorded: retry sends it again safely.`,
        );
      } else {
        submission.settle();
        setRetry(false);
        setProblem(explain(error));
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={TITLE[form]}
      size="M"
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui={`ui.subscriber.prepaid.${form}`}
      context={
        <>
          {account.customerName} · <Id id={account.id} prefix="BA-" />
        </>
      }
      footer={
        <>
          {problem !== null && (
            <div className="footer-push" role={retry ? 'alert' : undefined}>
              <Note tone={retry ? 'warning' : 'negative'} lead={retry ? '' : 'Refused.'}>
                {problem}
              </Note>
            </div>
          )}
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <MoneySubmitButton
            variant="primary"
            busy={busy}
            busyLabel={
              form === 'topup' ? 'Topping up…' : form === 'adjust' ? 'Applying…' : 'Holding…'
            }
            disabled={!valid || busy}
            onClick={() => void submit()}
          >
            {retry
              ? 'Retry'
              : form === 'adjust'
                ? 'Apply adjustment'
                : `${form === 'topup' ? 'Top up' : 'Hold'}${amount !== null && cents > 0 ? ` ${formatCents(cents)} ${account.currency}` : ''}`}
          </MoneySubmitButton>
        </>
      }
    >
      <fieldset className="bss-form bss-write-fields" disabled={busy}>
        <p className="bss-secondary">
          Available {formatCents(balance.availableCents)} {account.currency} · reserved{' '}
          {formatCents(balance.reservedCents)}
        </p>
        {form === 'adjust' && (
          <section>
            <Field label="Direction">
              <Segmented
                label="Adjustment direction"
                value={direction}
                onChange={setDirection}
                options={[
                  { key: 'credit', label: 'Credit' },
                  { key: 'debit', label: 'Debit' },
                ]}
              />
            </Field>
          </section>
        )}
        <section>
          <Field label="Amount">
            <MoneyField
              currency={account.currency}
              aria-label="Amount"
              placeholder="0.00"
              value={amountText}
              onChange={(event) => setAmountText(event.target.value)}
            />
          </Field>
          {amountText.trim() !== '' && amount === null && (
            <Note tone="negative">Enter an amount with at most 2 decimals.</Note>
          )}
          {limited && amount !== null && cents > spendable && (
            <Note tone="negative">Enter at most the available {formatCents(spendable)}.</Note>
          )}
        </section>
        {form === 'topup' && (
          <section>
            <Field label="Payment method">
              <Segmented
                label="Payment method"
                value={method}
                onChange={setMethod}
                options={[...METHODS, 'other'].map((m) => ({
                  key: m,
                  label: m === 'other' ? 'Other' : m,
                }))}
              />
            </Field>
            {method === 'other' && (
              <Field label="Payment method name">
                <TextInput
                  aria-label="Payment method name"
                  value={other}
                  onChange={(event) => setOther(event.target.value)}
                />
              </Field>
            )}
          </section>
        )}
        {form === 'hold' && (
          <section>
            <Field label="Holds through (optional)">
              <TextInput
                mono
                width={180}
                aria-label="Hold expires on"
                placeholder="YYYY-MM-DD"
                value={expiresOn}
                onChange={(event) => setExpiresOn(event.target.value)}
              />
            </Field>
            {!expiryShape && <Note tone="negative">Enter a date as YYYY-MM-DD.</Note>}
            {expiryPast && <Note tone="negative">Enter today or a later business date.</Note>}
          </section>
        )}
        <section>
          <Field label="Reason (optional)">
            <TextInput
              aria-label="Reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </section>
        <div className="money-balance-line" data-ui="ui.subscriber.prepaid.balanceAfter">
          Balance after{' '}
          <Amount
            cents={
              (form === 'hold' ? balance.availableCents : balance.ledgerCents) +
              (kind === 'debit' || kind === 'hold' ? -cents : cents)
            }
            currency={account.currency}
          />
        </div>
      </fieldset>
    </Dialog>
  );
}
