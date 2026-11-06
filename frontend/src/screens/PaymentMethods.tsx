import { useRef, useState } from 'react';
import {
  CARD_BRANDS,
  createCard,
  loadAutoTopup,
  loadCards,
  loadCardTopups,
  makeDefault,
  removeCard,
  retryPayment,
  saveAutoTopup,
  stubToken,
  submitCardTopup,
  type AutoTopup,
  type PaymentCommand,
  type SavedCard,
} from '../api/cards';
import type { Account } from '../api/bss';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { formatAmount, parseAmount, positiveAmountError, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Note } from '../ui/Note';
import { ErrorBlock } from '../ui/states';
import {
  Button,
  Checkbox,
  Dialog,
  Field,
  KeyValue,
  MoneyField,
  Panel,
  SelectField,
  submitDialogPrimary,
  Tag,
  TextInput,
  useToast,
} from '../ui/kit';
import { uncertain, useSubmissionKey } from './submissionKey';

// Saved cards, "Top up by card" and the auto top-up rule of a billing account
// (docs/decisions/payments.md). A card number is never entered or sent: a card is saved with
// a stub token, brand, last four digits and expiry. A card top-up only queues a charge; the
// balance changes when the gateway confirms.

const STATE_LABEL: Record<PaymentCommand['state'], string> = {
  pending: 'Pending',
  sent: 'Sent to the gateway',
  confirmed: 'Confirmed',
  declined: 'Declined',
  exhausted: 'Exhausted',
  cancelled: 'Cancelled',
};

const cardLabel = (card: SavedCard) =>
  `${card.name} · ${card.brand ?? 'card'} ending ${card.lastFourDigits ?? '----'}`;

function money(value: { value: number; unit: string } | null) {
  return value === null ? '—' : `${formatAmount(value.value)} ${value.unit}`;
}

export function PaymentMethodsPanel({
  account,
  version,
  onChanged,
}: {
  account: Account;
  version: number;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [refresh, setRefresh] = useState(0);
  const [adding, setAdding] = useState(false);
  const [topping, setTopping] = useState(false);
  const [rule, setRule] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const prepaid = account.ratingType === 'prepaid';
  const key = `${account.id}:${version}:${refresh}`;
  const cards = useResource(`cards:${key}`, (signal) => loadCards(account.id, signal));
  const auto = useResource(`auto-topup:${key}`, (signal) =>
    prepaid ? loadAutoTopup(account.id, signal) : Promise.resolve(null),
  );
  const reload = () => setRefresh((n) => n + 1);
  const usable = account.state === 'Active';

  async function act(work: () => Promise<unknown>, message: string) {
    setProblem(null);
    try {
      await work();
      toast(message);
      reload();
    } catch (error) {
      setProblem(explain(error));
    }
  }

  return (
    <div data-ui="ui.subscriber.paymentMethods">
      <Panel
        title="Payment methods"
        right={
          <>
            {prepaid && (
              <Button
                disabled={!usable || cards.status !== 'ok' || cards.data.length === 0}
                onClick={() => setTopping(true)}
              >
                Top up by card
              </Button>
            )}
            <Button variant="secondary" disabled={!usable} onClick={() => setAdding(true)}>
              Add card
            </Button>
          </>
        }
      >
        {problem !== null && <Note tone="negative">{problem}</Note>}
        {cards.status === 'error' && (
          <ErrorBlock title="Failed to load cards" error={cards.error} onRetry={cards.retry} />
        )}
        {cards.status === 'loading' && <p className="bss-secondary">Loading cards…</p>}
        {cards.status === 'ok' && cards.data.length === 0 && (
          <p className="bss-secondary">No saved cards. Add one to top up by card.</p>
        )}
        {cards.status === 'ok' && (
          <ul className="bss-card-list">
            {cards.data.map((card) => (
              <li key={card.id} data-ui="ui.subscriber.paymentMethods.card">
                <span>{cardLabel(card)}</span>{' '}
                {card.expirationDate !== null && (
                  <span className="bss-secondary">expires {card.expirationDate.slice(0, 7)}</span>
                )}{' '}
                {card.isPreferred ? (
                  <Tag>Default</Tag>
                ) : (
                  <Button
                    onClick={() =>
                      void act(() => makeDefault(card.id), `${card.name} is now the default`)
                    }
                  >
                    Make default
                  </Button>
                )}{' '}
                <Button
                  aria-label={`Remove ${card.name}`}
                  onClick={() => void act(() => removeCard(card.id), `${card.name} removed`)}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {prepaid && (
        <Panel
          title="Auto top-up"
          right={
            <Button
              disabled={!usable || auto.status !== 'ok'}
              onClick={() => setRule(true)}
              data-ui="ui.subscriber.autoTopup.edit"
            >
              Edit
            </Button>
          }
        >
          {auto.status === 'error' && (
            <ErrorBlock
              title="Failed to load auto top-up"
              error={auto.error}
              onRetry={auto.retry}
            />
          )}
          {auto.status === 'ok' && auto.data !== null && (
            <AutoSummary rule={auto.data} cards={cards.status === 'ok' ? cards.data : null} />
          )}
        </Panel>
      )}
      {adding && (
        <AddCardDialog
          account={account}
          onClose={() => setAdding(false)}
          onSaved={(card) => {
            setAdding(false);
            toast(`${card.name} saved`);
            reload();
            onChanged();
          }}
        />
      )}
      {topping && cards.status === 'ok' && (
        <CardTopupDialog
          account={account}
          cards={cards.data}
          onClose={() => {
            setTopping(false);
            onChanged();
          }}
        />
      )}
      {rule && auto.status === 'ok' && auto.data !== null && (
        <AutoTopupDialog
          account={account}
          rule={auto.data}
          hasDefault={cards.status === 'ok' && cards.data.some((c) => c.isPreferred)}
          onClose={() => setRule(false)}
          onSaved={() => {
            setRule(false);
            toast('Auto top-up saved');
            reload();
          }}
        />
      )}
    </div>
  );
}

function AutoSummary({ rule, cards }: { rule: AutoTopup; cards: SavedCard[] | null }) {
  const noDefault = cards !== null && !cards.some((c) => c.isPreferred);
  return (
    <div data-ui="ui.subscriber.autoTopup">
      <KeyValue
        items={[
          ['Status', rule.enabled ? 'On' : 'Off'],
          ['When available is below', money(rule.threshold)],
          ['Top up by', money(rule.amount)],
        ]}
      />
      {rule.enabled && noDefault && (
        <Note tone="warning">No default card is saved: nothing is charged until one is.</Note>
      )}
    </div>
  );
}

function AddCardDialog({
  account,
  onClose,
  onSaved,
}: {
  account: Account;
  onClose: () => void;
  onSaved: (card: SavedCard) => void;
}) {
  const [name, setName] = useState('');
  const [brand, setBrand] = useState<string>(CARD_BRANDS[0]);
  const [digits, setDigits] = useState('');
  const [month, setMonth] = useState('');
  const [year, setYear] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const saving = useRef(false);
  const token = useRef<string | null>(null);

  const m = Number(month);
  const y = Number(year);
  const valid =
    name.trim() !== '' &&
    /^\d{4}$/.test(digits) &&
    /^\d{1,2}$/.test(month) &&
    m >= 1 &&
    m <= 12 &&
    /^\d{4}$/.test(year) &&
    y >= 2000;

  async function submit() {
    if (!valid || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    token.current ??= stubToken();
    try {
      onSaved(
        await createCard(
          {
            accountId: account.id,
            name: name.trim(),
            brand,
            lastFourDigits: digits,
            month: m,
            year: y,
          },
          token.current,
        ),
      );
    } catch (error) {
      setProblem(explain(error));
      // a refusal makes the next try a new card; an unknown outcome reuses the token (409
      // then says it was saved)
      if (!uncertain(error)) token.current = null;
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      title="Add card"
      size="M"
      ui="ui.subscriber.paymentMethods.add"
      onSubmit={submitDialogPrimary}
      onClose={() => {
        if (!saving.current) onClose();
      }}
      footer={
        <>
          {problem !== null && (
            <div className="footer-push" role="alert">
              <Note tone="negative" lead="Refused.">
                {problem}
              </Note>
            </div>
          )}
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={!valid || busy}
            onClick={() => void submit()}
          >
            Save card
          </Button>
        </>
      }
    >
      <fieldset className="bss-form" disabled={busy}>
        <Note tone="info">
          Admin entry saves a stub token. A card number or security code is never entered or sent.
        </Note>
        <Field label="Name">
          <TextInput aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Brand">
          <SelectField aria-label="Brand" value={brand} onChange={(e) => setBrand(e.target.value)}>
            {CARD_BRANDS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </SelectField>
        </Field>
        <Field label="Last four digits">
          <TextInput
            aria-label="Last four digits"
            mono
            inputMode="numeric"
            maxLength={4}
            value={digits}
            onChange={(e) => setDigits(e.target.value)}
          />
        </Field>
        <Field label="Expiry month (1-12)">
          <TextInput
            aria-label="Expiry month"
            mono
            inputMode="numeric"
            maxLength={2}
            value={month}
            onChange={(e) => setMonth(e.target.value)}
          />
        </Field>
        <Field label="Expiry year">
          <TextInput
            aria-label="Expiry year"
            mono
            inputMode="numeric"
            maxLength={4}
            value={year}
            onChange={(e) => setYear(e.target.value)}
          />
        </Field>
      </fieldset>
    </Dialog>
  );
}

function CardTopupDialog({
  account,
  cards,
  onClose,
}: {
  account: Account;
  cards: SavedCard[];
  onClose: () => void;
}) {
  const [amountText, setAmountText] = useState('');
  const [cardId, setCardId] = useState(cards.find((c) => c.isPreferred)?.id ?? cards[0]?.id ?? '');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [retry, setRetry] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const saving = useRef(false);
  const submission = useSubmissionKey();
  const charges = useResource(`card-topups:${account.id}:${refresh}`, (signal) =>
    loadCardTopups(account.id, signal),
  );
  const amount = parseAmount(amountText);
  const valid = amount !== null && toCents(Number(amount)) > 0 && cardId !== '';

  async function submit() {
    if (!valid || amount === null || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    const key = submission.keyFor(`${account.id}|${cardId}|${amount}`);
    try {
      await submitCardTopup(account.id, amount, account.currency, cardId, key);
      submission.settle();
      setRetry(false);
      setAmountText('');
      setRefresh((n) => n + 1);
    } catch (error) {
      if (uncertain(error)) {
        setRetry(true);
        setProblem(`${explain(error)}. Unsure whether it was queued: retry sends it again safely.`);
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

  async function retryCharge(id: string) {
    setProblem(null);
    try {
      await retryPayment(id);
      setRefresh((n) => n + 1);
    } catch (error) {
      setProblem(explain(error));
    }
  }

  return (
    <Dialog
      title="Top up by card"
      size="M"
      ui="ui.subscriber.paymentMethods.topup"
      onSubmit={submitDialogPrimary}
      onClose={() => {
        if (!saving.current) onClose();
      }}
      footer={
        <>
          {problem !== null && (
            <div className="footer-push" role="alert">
              <Note tone={retry ? 'warning' : 'negative'} lead={retry ? '' : 'Refused.'}>
                {problem}
              </Note>
            </div>
          )}
          <Button onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={!valid || busy}
            onClick={() => void submit()}
          >
            {retry ? 'Retry' : 'Charge card'}
          </Button>
        </>
      }
    >
      <fieldset className="bss-form" disabled={busy}>
        <p className="bss-secondary">
          The balance is unchanged until the gateway confirms the charge.
        </p>
        <Field label="Card">
          <SelectField aria-label="Card" value={cardId} onChange={(e) => setCardId(e.target.value)}>
            {cards.map((card) => (
              <option key={card.id} value={card.id}>
                {cardLabel(card)}
                {card.isPreferred ? ' (default)' : ''}
              </option>
            ))}
          </SelectField>
        </Field>
        <Field label="Amount">
          <MoneyField
            currency={account.currency}
            aria-label="Amount"
            placeholder="0.00"
            value={amountText}
            onChange={(e) => setAmountText(e.target.value)}
          />
        </Field>
        {positiveAmountError(amountText) && (
          <Note tone="negative">{positiveAmountError(amountText)}</Note>
        )}
      </fieldset>
      <h3>Card charges</h3>
      {charges.status === 'error' && (
        <ErrorBlock title="Failed to load charges" error={charges.error} onRetry={charges.retry} />
      )}
      {charges.status === 'ok' && charges.data.length === 0 && (
        <p className="bss-secondary">No card charges yet.</p>
      )}
      {charges.status === 'ok' && charges.data.length > 0 && (
        <ul data-ui="ui.subscriber.paymentMethods.charges">
          {charges.data.map((charge) => (
            <li key={charge.id}>
              <Amount cents={toCents(charge.amount.value)} currency={charge.amount.unit} />{' '}
              <Tag>{STATE_LABEL[charge.state]}</Tag>
              {charge.declineReason !== null && <span> Declined: {charge.declineReason}</span>}
              {charge.lastError !== null && charge.state !== 'confirmed' && (
                <span> Last error: {charge.lastError}</span>
              )}
              {charge.state === 'exhausted' && (
                <>
                  {' '}
                  <Button onClick={() => void retryCharge(charge.id)}>Retry</Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      <Button onClick={() => setRefresh((n) => n + 1)}>Refresh</Button>
    </Dialog>
  );
}

function AutoTopupDialog({
  account,
  rule,
  hasDefault,
  onClose,
  onSaved,
}: {
  account: Account;
  rule: AutoTopup;
  hasDefault: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [enabled, setEnabled] = useState(rule.enabled);
  const [thresholdText, setThresholdText] = useState(
    rule.threshold === null ? '' : rule.threshold.value.toFixed(2),
  );
  const [amountText, setAmountText] = useState(
    rule.amount === null ? '' : rule.amount.value.toFixed(2),
  );
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const threshold = parseAmount(thresholdText);
  const amount = parseAmount(amountText);
  const cents = (v: string | null) => (v === null ? 0 : toCents(Number(v)));
  const tooSmall =
    enabled && threshold !== null && amount !== null && cents(amount) < cents(threshold);
  const valid =
    !enabled || (threshold !== null && amount !== null && cents(threshold) > 0 && !tooSmall);

  async function submit() {
    if (!valid || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await saveAutoTopup(
        account.id,
        enabled && threshold !== null && amount !== null
          ? { enabled: true, threshold, amount, currency: account.currency }
          : { enabled: false },
      );
      onSaved();
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title="Auto top-up"
      size="M"
      ui="ui.subscriber.autoTopup.dialog"
      onSubmit={submitDialogPrimary}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          {problem !== null && (
            <div className="footer-push" role="alert">
              <Note tone="negative" lead="Refused.">
                {problem}
              </Note>
            </div>
          )}
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={!valid || busy}
            onClick={() => void submit()}
          >
            Save
          </Button>
        </>
      }
    >
      <fieldset className="bss-form" disabled={busy}>
        <Checkbox
          label="Top up automatically"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        {enabled && !hasDefault && (
          <Note tone="warning">No default card is saved: nothing is charged until one is.</Note>
        )}
        <Field label="When available is below">
          <MoneyField
            currency={account.currency}
            aria-label="Threshold"
            placeholder="0.00"
            value={thresholdText}
            onChange={(e) => setThresholdText(e.target.value)}
          />
        </Field>
        <Field label="Top up by">
          <MoneyField
            currency={account.currency}
            aria-label="Top-up amount"
            placeholder="0.00"
            value={amountText}
            onChange={(e) => setAmountText(e.target.value)}
          />
        </Field>
        {tooSmall && <Note tone="negative">The amount must be at least the threshold.</Note>}
      </fieldset>
    </Dialog>
  );
}
