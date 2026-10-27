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
import { accountLabel } from '../format/ids';
import { formatCents, parseAmount, toCents } from '../format/money';
import { Button, Modal, Segmented, TextInput } from '../ui/kit';
import { uncertain, useSubmissionKey } from './submissionKey';

// The money forms of a prepaid account: top-up, adjustment (credit or debit) and operator
// hold (docs/decisions/prepaid.md P4d-P4f, frontend.md P6b). Each submission carries one
// Idempotency-Key. After an uncertain outcome (no response, a 5xx) the same unchanged form
// is sent again with the same key, so the server answers with what it stored instead of
// doing it twice; an edited form, a refusal or a success starts a new key.

export type MoneyForm = 'topup' | 'adjust' | 'hold';

const METHODS = ['bankTransfer', 'card', 'cash'];

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
    <Modal
      title={TITLE[form]}
      width={560}
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui={`ui.subscriber.prepaid.${form}`}
      subtitle={`${account.customerName} · ${accountLabel(account.id)}`}
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!valid || busy} onClick={() => void submit()}>
            {retry ? 'Retry' : TITLE[form]}
          </Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <p className="bss-secondary">
          Available {formatCents(balance.availableCents)} {account.currency} · reserved{' '}
          {formatCents(balance.reservedCents)}
        </p>
        {form === 'adjust' && (
          <section>
            <div className="bss-step-head">Direction</div>
            <Segmented
              label="Adjustment direction"
              value={direction}
              onChange={setDirection}
              options={[
                { key: 'credit', label: 'Credit' },
                { key: 'debit', label: 'Debit' },
              ]}
            />
          </section>
        )}
        <section>
          <div className="bss-step-head">Amount</div>
          <TextInput
            mono
            width={180}
            aria-label="Amount"
            placeholder="0.00"
            value={amountText}
            onChange={(event) => setAmountText(event.target.value)}
          />{' '}
          <span className="bss-secondary">{account.currency}</span>
          {amountText.trim() !== '' && amount === null && (
            <Note tone="negative">Enter an amount with at most 2 decimals.</Note>
          )}
          {limited && amount !== null && cents > spendable && (
            <Note tone="negative">Enter at most the available {formatCents(spendable)}.</Note>
          )}
        </section>
        {form === 'topup' && (
          <section>
            <div className="bss-step-head">Payment method</div>
            <Segmented
              label="Payment method"
              value={method}
              onChange={setMethod}
              options={[...METHODS, 'other'].map((m) => ({
                key: m,
                label: m === 'other' ? 'Other' : m,
              }))}
            />
            {method === 'other' && (
              <TextInput
                aria-label="Payment method name"
                value={other}
                onChange={(event) => setOther(event.target.value)}
              />
            )}
          </section>
        )}
        {form === 'hold' && (
          <section>
            <div className="bss-step-head">Holds through (optional)</div>
            <TextInput
              mono
              width={180}
              aria-label="Hold expires on"
              placeholder="YYYY-MM-DD"
              value={expiresOn}
              onChange={(event) => setExpiresOn(event.target.value)}
            />
            {!expiryShape && <Note tone="negative">Enter a date as YYYY-MM-DD.</Note>}
            {expiryPast && <Note tone="negative">Enter today or a later business date.</Note>}
          </section>
        )}
        <section>
          <div className="bss-step-head">Reason (optional)</div>
          <TextInput
            aria-label="Reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </section>
      </div>
    </Modal>
  );
}
