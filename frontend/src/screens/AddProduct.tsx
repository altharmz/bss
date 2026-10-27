import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useState } from 'react';
import { requiresNumber } from '../api/numbers';
import { NumberPicker } from './Numbers';
import {
  loadFirstCharge,
  loadSellableOfferings,
  orderAdd,
  replaceDraft,
  type Account,
  type FirstCharge,
} from '../api/bss';
import { ApiError, explain, newIdempotencyKey } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { Link } from '../app/router';
import { addDays, isIsoDate, lastDay } from '../format/dates';
import { accountLabel } from '../format/ids';
import { formatAmount, formatCents, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Button, Modal, TextInput, useToast } from '../ui/kit';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import type { PendingOrder } from '../api/commands';

// Modal 5. The first-charge preview is the backend's dry run (the stage 3 core on the
// hypothetical product): the UI shows what will be charged, it never computes it.
// Nothing is charged on confirmation - the order completes now, the charge is written
// by the billing run of max(start, tomorrow) and billed on the next cycle day from there.

const OFFER_COLUMNS = [
  { key: 'radio', label: '', width: '28px' },
  { key: 'name', label: '', width: '1fr' },
  { key: 'spec', label: '', width: '170px' },
  { key: 'price', label: '', width: '150px', align: 'right' as const, mono: true },
];

function runOf(start: string, today: string): string {
  return start > today ? start : addDays(today, 1);
}

/** A draft being edited: Add product reopens with its offering, start date and number, and
 * saves them back with a PATCH instead of placing an order. */
export interface DraftToEdit {
  orderId: string;
  itemId: string;
  offeringId: string;
  start: string | null; // business date; null = none requested yet
  numberId: string;
}

export function AddProductModal({
  account,
  draft,
  onDraftSaved,
  activeOfferingIds,
  onClose,
  onOrdered,
  onTopUp,
}: {
  account: Account;
  activeOfferingIds: Set<string>;
  onClose: () => void;
  /** Close Add product and open the card's Top-up form with this decimal amount typed in;
   * absent where there is no card behind the dialog (a new subscriber's first product). */
  onTopUp?: (amount: string) => void;
  onOrdered: (message: string, accepted?: PendingOrder) => void;
  /** Edit this draft instead of placing a new order. */
  draft?: DraftToEdit;
  /** A draft was saved (new or edited); the caller closes or reloads. */
  onDraftSaved?: () => void;
}) {
  const context = useSystemContext();
  const toast = useToast();
  const today = context.businessDate;
  const [filter, setFilter] = useState('');
  const [offeringId, setOfferingId] = useState<string | null>(draft?.offeringId ?? null);
  const [start, setStart] = useState(
    draft?.start != null && draft.start >= today ? draft.start : today,
  );
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string[]>([]);
  const [numberId, setNumberId] = useState(draft?.numberId ?? '');
  const [uncertain, setUncertain] = useState(false);

  const offerings = useResource('offerings', (signal) =>
    loadSellableOfferings(context.now, account.currency, signal),
  );
  const startValid = isIsoDate(start) && start >= today;
  const previewKey = offeringId !== null && startValid ? `${offeringId}|${start}` : 'none';
  const preview = useResource<FirstCharge | null>(previewKey, (signal) =>
    offeringId !== null && startValid
      ? loadFirstCharge(account.id, offeringId, start, signal)
      : Promise.resolve(null),
  );
  const first = preview.status === 'ok' ? preview.data : null;

  const q = filter.trim().toLowerCase();
  const visible =
    offerings.status === 'ok'
      ? offerings.data.filter(
          (o) => !q || o.name.toLowerCase().includes(q) || (o.code ?? '').toLowerCase().includes(q),
        )
      : [];
  const chosen =
    offerings.status === 'ok' ? offerings.data.find((o) => o.id === offeringId) : undefined;
  const requirement = useResource(
    `number-requirement:${chosen?.specificationId ?? ''}`,
    (signal) =>
      chosen?.specificationId
        ? requiresNumber(chosen.specificationId, signal)
        : Promise.resolve(false),
  );
  const needsNumber = requirement.status === 'ok' && requirement.data;
  const ready = first !== null && requirement.status === 'ok' && (!needsNumber || numberId !== '');

  const draftReady =
    chosen !== undefined &&
    startValid &&
    requirement.status === 'ok' &&
    (!needsNumber || numberId !== '');

  async function saveDraft() {
    if (offeringId === null || chosen === undefined) return;
    setBusy(true);
    setProblem([]);
    try {
      if (draft !== undefined) {
        await replaceDraft(
          draft.orderId,
          offeringId,
          start,
          context.timeZone,
          needsNumber ? numberId : undefined,
          draft.itemId,
        );
        toast('Draft updated');
      } else {
        await orderAdd(
          account.id,
          offeringId,
          start,
          context.timeZone,
          needsNumber ? numberId : undefined,
          newIdempotencyKey(),
        );
        toast(
          <>
            Draft saved · <Link href={`/orders?account=${account.id}`}>Open in Orders</Link>
          </>,
        );
      }
      (onDraftSaved ?? onClose)();
    } catch (error) {
      setProblem([
        draft !== undefined
          ? explain(error)
          : `${explain(error)}. Close and check Orders before saving again.`,
      ]);
      if (
        draft === undefined &&
        !(error instanceof ApiError && error.status < 500 && error.status > 0)
      )
        setUncertain(true);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (first === null || offeringId === null || chosen === undefined) return;
    setBusy(true);
    setProblem([]);
    try {
      const result = await orderAdd(
        account.id,
        offeringId,
        start,
        context.timeZone,
        needsNumber ? numberId : undefined,
      );
      if (result.state === 'completed') {
        onOrdered(
          `${chosen.name} ordered · first charge ${formatAmount(first.amount.value)}` +
            ` ${first.amount.unit} in the run of ${first.chargeRunDate}`,
        );
      } else if (result.state === 'inProgress') {
        // a prepaid order: accepted, and the billing worker completes it (OrderWatch follows)
        onOrdered(`${chosen.name} ordered: accepted, the billing worker completes it shortly`, {
          id: result.id,
          label: `Order for ${chosen.name}`,
        });
      } else {
        setProblem(result.reasons.length ? result.reasons : [`The order was ${result.state}.`]);
      }
    } catch (error) {
      setProblem([`${explain(error)}. Close and check the subscriber before submitting again.`]);
      setUncertain(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title={draft === undefined ? 'Add product' : 'Edit draft'}
      width={720}
      onClose={onClose}
      ui="ui.subscriber.addProduct"
      subtitle={`${account.customerName} · ${accountLabel(account.id)} · billing day ${account.cycleDay}`}
      footer={
        <>
          {problem.length > 0 && (
            <Note tone="negative" className="footer-push">
              {problem.join(' ')}
            </Note>
          )}
          <Button onClick={onClose}>Cancel</Button>
          {draft === undefined ? (
            <>
              <Button
                data-ui="ui.addProduct.saveDraft"
                disabled={!draftReady || busy || uncertain}
                onClick={() => void saveDraft()}
              >
                Save as draft
              </Button>
              <Button
                variant="primary"
                disabled={!ready || busy || uncertain}
                onClick={() => void confirm()}
              >
                {start === today ? 'Activate' : `Activate from ${start}`}
              </Button>
            </>
          ) : (
            <Button
              variant="primary"
              data-ui="ui.addProduct.saveDraftChanges"
              disabled={!draftReady || busy}
              onClick={() => void saveDraft()}
            >
              Save draft
            </Button>
          )}
        </>
      }
    >
      <div className="bss-modal-body">
        <section>
          <div className="bss-step-head">
            1. Offering <span className="caption">Active offerings only</span>
            <TextInput
              className="filter"
              width={230}
              placeholder="Filter offerings"
              aria-label="Filter offerings"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
          <div className="bss-offer-list" role="radiogroup" aria-label="Offering">
            {offerings.status === 'loading' && <SkeletonRows columns={OFFER_COLUMNS} rows={4} />}
            {offerings.status === 'error' && (
              <ErrorBlock
                title="Failed to load offerings"
                error={offerings.error}
                onRetry={offerings.retry}
              />
            )}
            {offerings.status === 'ok' && visible.length === 0 && (
              <div className="bss-state">
                <div className="text">No active offerings match.</div>
              </div>
            )}
            {visible.map((offer) => (
              <div
                key={offer.id}
                className="bss-offer"
                role="radio"
                aria-checked={offer.id === offeringId}
                tabIndex={0}
                onClick={() => setOfferingId(offer.id)}
                onKeyDown={(event) => {
                  if (event.key === ' ' || event.key === 'Enter') setOfferingId(offer.id);
                }}
              >
                <span className="bss-radio" />
                <span>
                  <span className="bss-name">{offer.name}</span>
                  {activeOfferingIds.has(offer.id) && (
                    <span className="bss-tag">already active</span>
                  )}
                  {offer.code !== null && <div className="bss-code">{offer.code}</div>}
                </span>
                <span>{offer.specification}</span>
                <span className="num num-right">
                  {offer.priceCents === null
                    ? '—'
                    : `${formatCents(offer.priceCents)} / ${offer.interval ?? 'mo'}`}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="bss-step-head">2. Start date</div>
          {requirement.status === 'error' && (
            <ErrorBlock
              title="Failed to load number requirement"
              error={requirement.error}
              onRetry={requirement.retry}
            />
          )}
          {needsNumber && <NumberPicker value={numberId} onChange={setNumberId} />}
          <TextInput
            mono
            type="date"
            aria-label="Start date"
            min={today}
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
          <Note tone="info">
            {!startValid
              ? `the start date cannot be before ${today}`
              : start === today
                ? `today · the charge is written by the billing run of ${runOf(start, today)}`
                : `the charge is written by the billing run of ${start}`}
          </Note>
        </section>

        <section>
          <div className="bss-step-head">3. First charge preview</div>
          <div className="bss-preview">
            {offeringId === null ? (
              <Note tone="info">Select an offering to see the calculation</Note>
            ) : (
              <Preview
                preview={preview}
                account={account}
                start={start}
                today={today}
                onTopUp={onTopUp}
              />
            )}
          </div>
        </section>
      </div>
    </Modal>
  );
}

export function Preview({
  preview,
  account,
  start,
  today,
  onTopUp,
}: {
  preview: ReturnType<typeof useResource<FirstCharge | null>>;
  account: Account;
  start: string;
  today: string;
  onTopUp: ((amount: string) => void) | undefined;
}) {
  if (preview.status === 'loading') return <Note tone="info">Calculating…</Note>;
  if (preview.status === 'error') {
    return <Note tone="negative">{explain(preview.error)}</Note>;
  }
  const p = preview.data;
  if (p === null) return <Note tone="info">Select an offering to see the calculation</Note>;
  if (account.ratingType === 'prepaid' && p.required !== undefined) {
    return <PrepaidPreview p={p} start={start} today={today} onTopUp={onTopUp} />;
  }
  const price = formatAmount(p.price.value);
  const after = toCents(p.balanceAfter.value);
  return (
    <>
      <div className="bss-preview-grid">
        <dl>
          <dt>Period</dt>
          <dd>
            {p.period.start} – {lastDay(p.period.end)}
          </dd>
          <dt>Billable days</dt>
          <dd>
            {p.billableDays} of {p.periodDays}
            {p.prorated ? ' (prorated)' : ''}
          </dd>
          <dt>Offering price</dt>
          <dd>
            {price} {p.price.unit} / {p.recurringChargePeriod ?? 'mo'}
          </dd>
          <dt>Formula</dt>
          <dd>{p.prorated ? `${price} × ${p.billableDays} / ${p.periodDays}` : 'full period'}</dd>
        </dl>
        <dl>
          <dt>First charge</dt>
          <dd>
            <span className="first" data-testid="first-charge">
              {formatAmount(p.amount.value)} {p.amount.unit}
            </span>
            <div className="bss-secondary">
              in the run of {p.chargeRunDate} · on the bill of {p.billDate}
            </div>
          </dd>
          <dt>{p.next === null ? 'Then' : `Then from ${p.next.start}`}</dt>
          <dd>
            {p.next === null
              ? 'no price after this period'
              : `${formatAmount(p.next.amount.value)} ${p.next.amount.unit}`}
          </dd>
          <dt>Balance after this charge</dt>
          <dd>
            <Amount cents={after} currency={p.balanceAfter.unit} />
          </dd>
        </dl>
      </div>
      {p.exceedsCreditLimit && p.creditLimit !== null && (
        <Note tone="warning">
          Balance will exceed the credit limit of {formatAmount(p.creditLimit.value)}{' '}
          {p.creditLimit.unit}. Activation is allowed; services may be suspended.
        </Note>
      )}
      <div className="footnote">
        Prorated by days until the account’s next billing day (day {account.cycleDay}). Full price
        is charged every cycle after that. An order completed today is charged by tomorrow’s billing
        run at the earliest.
      </div>
    </>
  );
}

// A prepaid account pays the whole first interval, taxes included, in advance (docs/decisions/
// frontend.md, next-slices-design.md section 1). The figures are the backend's: required,
// available, shortfall and balanceAfter; nothing here is computed except the comparison with 0
// that decides which hint to show.
function PrepaidPreview({
  p,
  start,
  today,
  onTopUp,
}: {
  p: FirstCharge;
  start: string;
  today: string;
  onTopUp: ((amount: string) => void) | undefined;
}) {
  const now = start === today;
  const cur = p.amount.unit;
  const required = p.required ?? p.amount;
  const shortfallCents = p.shortfall === undefined ? 0 : toCents(p.shortfall.value);
  const short = shortfallCents > 0;
  return (
    <div data-ui="ui.addProduct.prepaidPreview">
      <div className="bss-preview-grid">
        <dl>
          <dt>Period</dt>
          <dd>
            {p.period.start} – {lastDay(p.period.end)}
          </dd>
          <dt>Offering price</dt>
          <dd>
            {formatAmount(p.price.value)} {p.price.unit} / {p.recurringChargePeriod ?? 'mo'}
          </dd>
          <dt>Charging</dt>
          <dd>paid in full in advance, no proration</dd>
        </dl>
        <dl>
          <dt>{now ? 'To pay now' : `To pay on ${start}`}</dt>
          <dd>
            <span className="first" data-testid="prepaid-required">
              {formatAmount(required.value)} {required.unit}
            </span>
          </dd>
          <dt>Available</dt>
          <dd>{p.available === undefined ? '—' : `${formatAmount(p.available.value)} ${cur}`}</dd>
          {short && p.shortfall !== undefined && (
            <>
              <dt>Shortfall</dt>
              <dd>
                <Amount cents={-shortfallCents} currency={p.shortfall.unit} />
              </dd>
            </>
          )}
          {now && (
            <>
              <dt>Ledger after</dt>
              <dd>
                <Amount cents={toCents(p.balanceAfter.value)} currency={p.balanceAfter.unit} />
              </dd>
            </>
          )}
        </dl>
      </div>
      {short && now && (
        <Note tone="warning">
          Insufficient funds: top up at least {formatCents(shortfallCents)} {cur}. The order is
          refused until the account has the funds.{' '}
          {onTopUp !== undefined && (
            <Button
              data-ui="ui.addProduct.topUpShortfall"
              onClick={() => onTopUp(formatCents(shortfallCents).replace(/,/g, ''))}
            >
              Top up {formatCents(shortfallCents)}
            </Button>
          )}
        </Note>
      )}
      {short && !now && (
        <Note tone="info">
          Funds are checked on {start}; nothing is held now. Top up before then.
        </Note>
      )}
      <div className="footnote">
        {now
          ? 'Submitting holds the full interval with its taxes; the billing worker charges it and renews at the paid-through date while funds last.'
          : `On ${start} the billing worker takes the interval from the funds available then.`}
      </div>
    </div>
  );
}
