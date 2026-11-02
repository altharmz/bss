import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useEffect, useEffectEvent, useRef, useState } from 'react';
import type { Account, PrepaidBalance } from '../api/bss';
import { explain } from '../api/client';
import {
  findReceiver,
  loadOwnNumbers,
  loadTransfer,
  submitTransfer,
  transferRequest,
  type ReceiverLookup,
} from '../api/transfer';
import { usePolled } from '../api/usePolled';
import { useResource } from '../api/useResource';
import { formatCents, parseAmount, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import {
  Button,
  Checkbox,
  Dialog,
  Field,
  KeyValue,
  MoneyField,
  Segmented,
  StatusPill,
  Tag,
  TextInput,
} from '../ui/kit';
import { uncertain, useSubmissionKey } from './submissionKey';
import { POLL_MS, watch, type Watched } from './watched';
import { WatchProgress } from './WatchProgress';
import { MoneySubmitButton } from './PrepaidMoney';

// Transfer of prepaid funds to another subscriber, by number (docs/decisions/frontend.md
// P6c1). Accepting a transfer moves nothing: the sender's funds are held and the billing
// worker completes it within about a minute, so the card then watches it (TransferWatch).
// A transfer is final: the confirmation step says so.

export function PrepaidTransferModal({
  account,
  balance,
  onClose,
  onAccepted,
}: {
  account: Account;
  balance: PrepaidBalance;
  onClose: () => void;
  onAccepted: (watched: Watched) => void;
}) {
  const own = useResource(`own-numbers:${account.id}`, (signal) =>
    loadOwnNumbers(account.id, signal),
  );
  const [senderId, setSenderId] = useState('');
  const [digits, setDigits] = useState('');
  const [lookup, setLookup] = useState<
    { state: 'idle' } | { state: 'busy' } | { state: 'error'; message: string } | ReceiverLookup
  >({ state: 'idle' });
  const [amountText, setAmountText] = useState('');
  const [reason, setReason] = useState('');
  const [step, setStep] = useState(1);
  const [acknowledged, setAcknowledged] = useState(false);
  const confirming = step === 3;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [retry, setRetry] = useState(false);
  const saving = useRef(false);
  const submission = useSubmissionKey();

  const numbers = own.status === 'ok' ? own.data : [];
  const sender = numbers.find((n) => n.id === senderId) ?? numbers[0];
  const receiver = 'found' in lookup && lookup.found ? lookup.receiver : null;
  const amount = parseAmount(amountText);
  const cents = amount === null ? 0 : toCents(Number(amount));
  const spendable = Math.max(0, balance.availableCents);

  const receiverProblem =
    receiver === null
      ? null
      : receiver.account.id === account.id
        ? 'That number belongs to this account.'
        : receiver.balance === null
          ? 'That subscriber is not prepaid, so it cannot receive a transfer.'
          : receiver.account.currency !== account.currency
            ? `That subscriber's currency is ${receiver.account.currency}, not ${account.currency}.`
            : null;
  const ready =
    sender !== undefined &&
    receiver !== null &&
    receiver.balance !== null &&
    receiverProblem === null &&
    amount !== null &&
    cents > 0 &&
    cents <= spendable &&
    reason.trim() !== '';

  async function find() {
    setLookup({ state: 'busy' });
    try {
      setLookup(await findReceiver(digits.trim(), new AbortController().signal));
    } catch (error) {
      setLookup({ state: 'error', message: explain(error) });
    }
  }

  async function submit() {
    if (!ready || amount === null || receiver?.balance == null || sender === undefined) return;
    if (saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    const request = transferRequest({
      senderNumberId: sender.id,
      receiverNumberId: receiver.numberId,
      bucketId: balance.bucketId,
      receiverBucketId: receiver.balance.bucketId,
      currency: account.currency,
      amount,
      reason,
    });
    const key = submission.keyFor(request.path + request.body);
    try {
      const stored = await submitTransfer(request, key);
      submission.settle();
      if (stored.status === 'failed') {
        setRetry(false);
        setProblem(stored.description ?? 'The transfer was refused.');
        return;
      }
      onAccepted(
        watch(
          'transfer',
          stored.id,
          `${formatCents(cents)} ${account.currency} to ${receiver.digits}`,
        ),
      );
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
      onSubmit={confirming ? undefined : submitDialogPrimary}
      title="Transfer funds"
      size="M"
      step={{ n: step, of: 3 }}
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui="ui.subscriber.prepaid.transfer"
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
          {confirming ? (
            <>
              <Button
                disabled={busy || retry}
                onClick={() => {
                  setStep(2);
                  setAcknowledged(false);
                  setProblem(null);
                }}
              >
                Back
              </Button>
              <MoneySubmitButton
                variant="primary"
                busy={busy}
                busyLabel="Transferring…"
                disabled={busy || !acknowledged}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') event.preventDefault();
                }}
                onClick={() => void submit()}
              >
                {retry ? 'Retry' : `Transfer ${formatCents(cents)} ${account.currency}`}
              </MoneySubmitButton>
            </>
          ) : (
            <>
              <Button disabled={step === 1} onClick={() => setStep(1)}>
                Back
              </Button>
              <Button
                variant="primary"
                disabled={
                  step === 1
                    ? sender === undefined ||
                      receiver === null ||
                      receiverProblem !== null ||
                      receiver.balance === null
                    : !ready
                }
                onClick={() => {
                  setStep(step + 1);
                  setAcknowledged(false);
                }}
              >
                {step === 1 ? 'Next' : 'Review transfer'}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="bss-modal-body">
        <p className="bss-secondary">
          Available {formatCents(balance.availableCents)} {account.currency} · reserved{' '}
          {formatCents(balance.reservedCents)}
        </p>
        {confirming && sender !== undefined && receiver !== null ? (
          <section data-ui="ui.subscriber.prepaid.transfer.confirm">
            <div
              className="money-transfer-summary"
              data-ui="ui.subscriber.prepaid.transfer.summary"
            >
              <span className="bss-secondary">You are transferring</span>
              <div className="money-transfer-figure">
                <Amount cents={cents} currency={account.currency} />
              </div>
              <p>
                From {account.customerName} · <Id id={account.id} prefix="BA-" /> · {sender.value}
              </p>
              <p>
                To {receiver.account.customerName} · <Id id={receiver.account.id} prefix="BA-" /> ·{' '}
                {receiver.digits}
              </p>
            </div>
            <Note tone="warning">
              This is final. A transfer can't be undone here. Correcting it needs a new transfer
              from the recipient, with their consent.
            </Note>
            <label
              className="money-acknowledgement"
              data-ui="ui.subscriber.prepaid.transfer.acknowledgement"
            >
              <Checkbox
                checked={acknowledged}
                disabled={busy}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
              I confirmed the amount and the recipient's number with the subscriber
            </label>
          </section>
        ) : (
          <fieldset className="bss-form bss-write-fields" disabled={busy}>
            {step === 1 && (
              <>
                <section>
                  <div className="bss-step-head">From number</div>
                  {own.status === 'loading' && (
                    <span className="bss-secondary">Loading numbers…</span>
                  )}
                  {own.status === 'error' && (
                    <Note tone="negative">Failed to load numbers ({explain(own.error)})</Note>
                  )}
                  {own.status === 'ok' && numbers.length === 0 && (
                    <Note tone="negative">
                      This account has no active product with a subscriber number, so it cannot send
                      a transfer. Only products whose specification requires a number carry one.
                    </Note>
                  )}
                  {numbers.length > 0 && (
                    <Segmented
                      label="From number"
                      value={sender?.id ?? ''}
                      onChange={setSenderId}
                      options={numbers.map((n) => ({ key: n.id, label: n.value }))}
                    />
                  )}
                </section>
                <section>
                  <div className="bss-step-head">To number</div>
                  <TextInput
                    mono
                    width={220}
                    aria-label="Receiver number"
                    placeholder="79991234001"
                    value={digits}
                    onChange={(event) => {
                      setDigits(event.target.value);
                      setLookup({ state: 'idle' });
                    }}
                  />{' '}
                  <Button
                    disabled={
                      !/^\d{8,15}$/.test(digits.trim()) ||
                      ('state' in lookup && lookup.state === 'busy')
                    }
                    onClick={() => void find()}
                  >
                    Find
                  </Button>
                  {'state' in lookup && lookup.state === 'busy' && (
                    <span className="bss-secondary"> Looking up…</span>
                  )}
                  {'state' in lookup && lookup.state === 'error' && (
                    <Note tone="negative">Lookup failed ({lookup.message})</Note>
                  )}
                  {'found' in lookup && !lookup.found && <Note tone="negative">{lookup.why}</Note>}
                  {receiver !== null && (
                    <div
                      className="money-recipient"
                      data-ui="ui.subscriber.prepaid.transfer.recipient"
                    >
                      {receiver.account.customerName}{' '}
                      <StatusPill kind="account" status={receiver.account.state} />{' '}
                      <Id id={receiver.account.id} prefix="BA-" /> <span>{receiver.digits}</span>{' '}
                      <Tag>{receiver.account.ratingType}</Tag>
                    </div>
                  )}
                  {receiverProblem !== null && <Note tone="warning">{receiverProblem}</Note>}
                </section>
                <p className="bss-secondary">
                  Only active prepaid accounts can receive a transfer.
                </p>
              </>
            )}
            {step === 2 && (
              <>
                <section>
                  <Field
                    label="Amount"
                    help={`Up to ${formatCents(spendable)} ${account.currency}, the available balance. Reserved money can't be moved.`}
                  >
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
                  {amount !== null && cents > spendable && (
                    <Note tone="negative">
                      Enter at most the available {formatCents(spendable)}.
                    </Note>
                  )}
                </section>
                <section>
                  <Field label="Reason">
                    <TextInput
                      aria-label="Reason"
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </Field>
                </section>
                <div data-ui="ui.subscriber.prepaid.transfer.balances">
                  <KeyValue
                    items={[
                      [
                        `${account.customerName} · available`,
                        <>
                          <Amount cents={balance.availableCents} currency={account.currency} /> →{' '}
                          <Amount
                            cents={balance.availableCents - cents}
                            currency={account.currency}
                          />
                        </>,
                      ],
                      ...(receiver?.balance
                        ? [
                            [
                              `${receiver.account.customerName} · ledger`,
                              <>
                                <Amount
                                  cents={receiver.balance.ledgerCents}
                                  currency={account.currency}
                                />{' '}
                                →{' '}
                                <Amount
                                  cents={receiver.balance.ledgerCents + cents}
                                  currency={account.currency}
                                />
                              </>,
                            ] as [string, React.ReactNode],
                          ]
                        : []),
                    ]}
                  />
                </div>
              </>
            )}
          </fieldset>
        )}
      </div>
    </Dialog>
  );
}

/** Follows an accepted transfer until the worker completes or refuses it: every 5 s, for at
 * most 3 minutes from the submission. Past the cap it says so plainly (the funds stay held
 * and the worker still finishes it) and offers a manual check. */
export function TransferWatch({
  watched,
  accountId,
  onSettled,
  onDismiss,
}: {
  watched: Watched;
  accountId?: string;
  onSettled: (message: string) => void;
  onDismiss: () => void;
}) {
  const transfer = usePolled(
    `transfer:${watched.id}`,
    (signal) => loadTransfer(watched.id, signal),
    (state) => state.status === 'created',
    POLL_MS,
    watched.until,
  );
  const status = transfer.status === 'ok' ? transfer.data.status : null;
  const description = transfer.status === 'ok' ? transfer.data.description : null;
  const settle = useEffectEvent(onSettled);
  useEffect(() => {
    if (status === 'completed') settle(`Transfer of ${watched.label} completed`);
    else if (status === 'failed') {
      settle(`Transfer of ${watched.label} failed: ${description ?? 'it was refused'}`);
    }
  }, [status, description, watched.label]);

  const late = status === 'created' && transfer.refreshError === null && transfer.capped;
  return (
    <div className="subscriber-watch" data-ui="ui.subscriber.prepaid.transferWatch" role="status">
      <WatchProgress
        lastCheckedAt={transfer.lastCheckedAt}
        phase={status === 'completed' ? 'completed' : status === 'failed' ? 'rejected' : 'waiting'}
      >
        {status === 'completed' || status === 'failed' ? (
          <>
            Transfer of {watched.label} {status}
          </>
        ) : transfer.status === 'error' || transfer.refreshError !== null ? (
          <>
            Could not check the transfer of {watched.label} (
            {explain(transfer.status === 'error' ? transfer.error : transfer.refreshError)}).{' '}
            <Button small onClick={transfer.retry}>
              Check again
            </Button>
          </>
        ) : late ? (
          <>
            The transfer of {watched.label} is still waiting for the billing worker. The amount
            stays held and the worker completes it on its next wake-up. Still running. It continues
            in{' '}
            <a
              className="bss-link"
              href={`/prepaid-commands${accountId ? `?account=${accountId}` : ''}`}
            >
              Prepaid commands
            </a>
            .{' '}
            <Button small onClick={transfer.refresh}>
              Check now
            </Button>{' '}
            <Button small onClick={onDismiss}>
              Dismiss
            </Button>
          </>
        ) : (
          <>Transfer of {watched.label} accepted, waiting for the billing worker…</>
        )}
      </WatchProgress>
    </div>
  );
}
