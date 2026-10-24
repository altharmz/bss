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
import { accountLabel } from '../format/ids';
import { formatCents, parseAmount, toCents } from '../format/money';
import { Button, Modal, Panel, Segmented, TextInput } from '../ui/kit';
import { uncertain, useSubmissionKey } from './submissionKey';
import { POLL_MS, watch, type Watched } from './watched';

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
  const [confirming, setConfirming] = useState(false);
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
    <Modal
      title="Transfer funds"
      width={560}
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui="ui.subscriber.prepaid.transfer"
      subtitle={`${account.customerName} · ${accountLabel(account.id)}`}
      footer={
        <>
          {problem !== null && (
            <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
              {problem}
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
                  setConfirming(false);
                  setProblem(null);
                }}
              >
                Back
              </Button>
              <Button variant="primary" disabled={busy} onClick={() => void submit()}>
                {retry ? 'Retry' : 'Confirm transfer'}
              </Button>
            </>
          ) : (
            <Button variant="primary" disabled={!ready} onClick={() => setConfirming(true)}>
              Review transfer
            </Button>
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
            <p>
              Transfer{' '}
              <strong>
                {formatCents(cents)} {account.currency}
              </strong>{' '}
              from {sender.value} to {receiver.digits} ({receiver.account.customerName}
              {', '}
              {accountLabel(receiver.account.id)})?
            </p>
            <p className="bss-secondary">
              A transfer is final. It cannot be cancelled or edited once confirmed; only a new
              transfer in the opposite direction reverses it. The amount is held on this account now
              and moves when the billing worker processes it, usually within a minute.
            </p>
          </section>
        ) : (
          <>
            <section>
              <div className="bss-step-head">From number</div>
              {own.status === 'loading' && <span className="bss-secondary">Loading numbers…</span>}
              {own.status === 'error' && (
                <div className="bss-inline-error">
                  Failed to load numbers ({explain(own.error)})
                </div>
              )}
              {own.status === 'ok' && numbers.length === 0 && (
                <div className="bss-inline-error">
                  This account has no active product with a number, so it cannot send a transfer.
                </div>
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
                <div className="bss-inline-error">Lookup failed ({lookup.message})</div>
              )}
              {'found' in lookup && !lookup.found && (
                <div className="bss-inline-error">{lookup.why}</div>
              )}
              {receiver !== null && (
                <div className="bss-secondary">
                  {receiver.account.customerName} · {accountLabel(receiver.account.id)}
                </div>
              )}
              {receiverProblem !== null && (
                <div className="bss-inline-error">{receiverProblem}</div>
              )}
            </section>
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
                <div className="bss-inline-error">Enter an amount with at most 2 decimals.</div>
              )}
              {amount !== null && cents > spendable && (
                <div className="bss-inline-error">
                  Enter at most the available {formatCents(spendable)}.
                </div>
              )}
            </section>
            <section>
              <div className="bss-step-head">Reason</div>
              <TextInput
                aria-label="Reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </section>
          </>
        )}
      </div>
    </Modal>
  );
}

/** Follows an accepted transfer until the worker completes or refuses it: every 5 s, for at
 * most 3 minutes from the submission. Past the cap it says so plainly (the funds stay held
 * and the worker still finishes it) and offers a manual check. */
export function TransferWatch({
  watched,
  onSettled,
  onDismiss,
}: {
  watched: Watched;
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

  if (status === 'completed' || status === 'failed') return null;
  const late = status === 'created' && transfer.refreshError === null && transfer.capped;
  return (
    <Panel>
      <div data-ui="ui.subscriber.prepaid.transferWatch" role="status">
        {transfer.status === 'error' || transfer.refreshError !== null ? (
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
            stays held and the worker completes it on its next wake-up.{' '}
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
      </div>
    </Panel>
  );
}
