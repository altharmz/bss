import { useState } from 'react';
import { requiresNumber } from '../api/numbers';
import { NumberPicker } from './Numbers';
import {
  loadFirstCharge,
  loadSellableOfferings,
  orderAdd,
  type Account,
  type FirstCharge,
} from '../api/bss';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { addDays, isIsoDate, lastDay } from '../format/dates';
import { accountLabel } from '../format/ids';
import { formatAmount, formatCents, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Button, Modal, TextInput } from '../ui/kit';
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

export function AddProductModal({
  account,
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
}) {
  const context = useSystemContext();
  const today = context.businessDate;
  const [filter, setFilter] = useState('');
  const [offeringId, setOfferingId] = useState<string | null>(null);
  const [start, setStart] = useState(today);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string[]>([]);
  const [numberId, setNumberId] = useState('');
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
      title="Add product"
      width={720}
      onClose={onClose}
      ui="ui.subscriber.addProduct"
      subtitle={`${account.customerName} · ${accountLabel(account.id)} · billing day ${account.cycleDay}`}
      footer={
        <>
          {problem.length > 0 && (
            <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
              {problem.join(' ')}
            </div>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!ready || busy || uncertain}
            onClick={() => void confirm()}
          >
            {start === today ? 'Activate' : `Activate from ${start}`}
          </Button>
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
          <span className="bss-hint">
            {!startValid
              ? `the start date cannot be before ${today}`
              : start === today
                ? `today · the charge is written by the billing run of ${runOf(start, today)}`
                : `the charge is written by the billing run of ${start}`}
          </span>
        </section>

        <section>
          <div className="bss-step-head">3. First charge preview</div>
          <div className="bss-preview">
            {offeringId === null ? (
              <div className="bss-hint">Select an offering to see the calculation</div>
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
  if (preview.status === 'loading') return <div className="bss-hint">Calculating…</div>;
  if (preview.status === 'error') {
    return (
      <div className="bss-inline-error" role="alert">
        {explain(preview.error)}
      </div>
    );
  }
  const p = preview.data;
  if (p === null) return <div className="bss-hint">Select an offering to see the calculation</div>;
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
        <div className="bss-warning" role="note">
          Balance will exceed the credit limit of {formatAmount(p.creditLimit.value)}{' '}
          {p.creditLimit.unit}. Activation is allowed; services may be suspended.
        </div>
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
        <div className="bss-warning" role="note">
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
        </div>
      )}
      {short && !now && (
        <div className="bss-hint" role="note">
          Funds are checked on {start}; nothing is held now. Top up before then.
        </div>
      )}
      <div className="footnote">
        {now
          ? 'Submitting holds the full interval with its taxes; the billing worker charges it and renews at the paid-through date while funds last.'
          : `On ${start} the billing worker takes the interval from the funds available then.`}
      </div>
    </div>
  );
}
