import { Footer } from './FinancialForms';
import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useRef, useState } from 'react';
import { requirements } from '../api/numbers';
import { imeiProblem } from '../api/devices';
import { createPortIn, type PortInData } from '../api/ports';
import { NumberPicker } from './Numbers';
import {
  loadFirstCharge,
  loadSellableOfferings,
  loadSettlement,
  midnightOf,
  orderAdd,
  replaceDraft,
  shownPrice,
  type Account,
  type FirstCharge,
  type SimType,
} from '../api/bss';
import { ApiError, explain, newIdempotencyKey } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { Link } from '../app/router';
import { addDays, clockTime, isIsoDate, lastDay } from '../format/dates';
import { accountLabel } from '../format/ids';
import { formatAmount, formatCents, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Button, DateField, Dialog, Field, SelectField, TextInput, useToast } from '../ui/kit';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { KeyValue } from '../ui/summaries';
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
  imsiId?: string;
  simType?: SimType;
  imei?: string;
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
  const [code, setCode] = useState<string | undefined>();
  const [numberId, setNumberId] = useState(draft?.numberId ?? '');
  const [imsiId, setImsiId] = useState(draft?.imsiId ?? '');
  // '' = a specific SIM from the picker; else the server picks one of that type from stock
  const [simChoice, setSimChoice] = useState<'' | SimType>(draft?.simType ?? '');
  // optional: the device of a prepaid line, checked by the network when the order is placed
  const [imei, setImei] = useState(draft?.imei ?? '');
  const [uncertain, setUncertain] = useState(false);
  // port-in: the customer brings a number. The port request is created first, then the order
  // takes the number it made; the request id is kept so a retried order does not create it twice
  const [porting, setPorting] = useState(false);
  const [port, setPort] = useState<PortInData>({
    number: '',
    carrierAccountNumber: '',
    transferPin: '',
    billingZip: '',
    firstName: '',
    lastName: '',
  });
  const [portedId, setPortedId] = useState<{ number: string; id: string } | null>(null);
  const setPortField = (key: keyof PortInData, value: string) =>
    setPort((p) => ({ ...p, [key]: value }));
  const portComplete =
    /^[1-9][0-9]{7,14}$/.test(port.number.trim()) &&
    /^[0-9]{5}(-[0-9]{4})?$/.test(port.billingZip.trim()) &&
    [port.carrierAccountNumber, port.transferPin, port.firstName, port.lastName].every(
      (v) => v.trim() !== '',
    );
  // B22: an edited draft opens with its offering row in view, once (jsdom has no scrolling)
  const scrolled = useRef(false);
  const scrollToDraft = (node: HTMLDivElement | null) => {
    if (node === null || scrolled.current) return;
    scrolled.current = true;
    node.scrollIntoView?.({ block: 'nearest' });
  };
  const [previewAnswer, setPreviewAnswer] = useState<{ key: string; at: string } | null>(null);

  const offerings = useResource('offerings', (signal) =>
    loadSellableOfferings(account.currency, signal),
  );
  const startValid = isIsoDate(start) && start >= today;
  const previewKey = offeringId !== null && startValid ? `${offeringId}|${start}` : 'none';
  const preview = useResource<FirstCharge | null>(previewKey, async (signal) => {
    if (offeringId === null || !startValid) return null;
    const data = await loadFirstCharge(account.id, offeringId, start, signal);
    if (!signal.aborted) {
      setPreviewAnswer({ key: previewKey, at: new Date().toISOString() });
    }
    return data;
  });
  const first = preview.status === 'ok' ? preview.data : null;
  const answeredAt = previewAnswer?.key === previewKey ? previewAnswer.at : null;

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
        ? requirements(chosen.specificationId, signal)
        : Promise.resolve({ msisdn: false, imsi: false }),
  );
  const needsNumber = requirement.status === 'ok' && requirement.data.msisdn;
  const needsImsi = requirement.status === 'ok' && requirement.data.imsi;
  // one reference per required kind
  const simType = needsImsi && simChoice !== '' ? simChoice : undefined;
  // a device is recorded on prepaid lines only (the server refuses it elsewhere)
  const asksDevice = account.ratingType === 'prepaid' && needsImsi;
  const typedImei = asksDevice ? imei.trim() : '';
  const imeiError = typedImei === '' ? null : imeiProblem(typedImei);
  const chosenCharacteristics = {
    ...(simType ? { simType } : {}),
    ...(typedImei ? { imei: typedImei } : {}),
  };
  const portingNumber = needsNumber && porting;
  const resources = [needsNumber && !porting && numberId, needsImsi && !simType && imsiId].filter(
    Boolean,
  ) as string[];
  const chosenResources = resources.length === 0 ? undefined : resources;
  const complete =
    (!needsNumber || (porting ? portComplete : numberId !== '')) &&
    (!needsImsi || simType !== undefined || imsiId !== '') &&
    imeiError === null;
  const shortNow =
    account.ratingType === 'prepaid' &&
    start === today &&
    first?.shortfall !== undefined &&
    toCents(first.shortfall.value) > 0;
  const ready = !shortNow && first !== null && requirement.status === 'ok' && complete;

  // a draft keeps no port request: the request is created when the order is placed
  const draftReady =
    chosen !== undefined && startValid && requirement.status === 'ok' && complete && !portingNumber;

  async function saveDraft() {
    if (offeringId === null || chosen === undefined) return;
    setBusy(true);
    setProblem([]);
    setCode(undefined);
    try {
      if (draft !== undefined) {
        const result = await replaceDraft(
          draft.orderId,
          offeringId,
          start,
          context.timeZone,
          chosenResources,
          draft.itemId,
          chosenCharacteristics,
        );
        if (result.state === 'rejected') {
          setProblem([result.refusal.problem]);
          setCode(result.refusal.code);
          return;
        }
        toast('Draft updated');
      } else {
        const result = await orderAdd(
          account.id,
          offeringId,
          start,
          context.timeZone,
          chosenResources,
          newIdempotencyKey(),
          chosenCharacteristics,
        );
        if (result.state === 'rejected') {
          setProblem([result.refusal.problem]);
          setCode(result.refusal.code);
          return;
        }
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
    if (!ready || first === null || offeringId === null || chosen === undefined) return;
    setBusy(true);
    setProblem([]);
    setCode(undefined);
    try {
      let ordered = chosenResources;
      if (portingNumber) {
        const value = port.number.trim();
        let id = portedId?.number === value ? portedId.id : undefined;
        if (id === undefined) {
          const created = await createPortIn({
            number: value,
            carrierAccountNumber: port.carrierAccountNumber.trim(),
            transferPin: port.transferPin.trim(),
            billingZip: port.billingZip.trim(),
            firstName: port.firstName.trim(),
            lastName: port.lastName.trim(),
          });
          if (!created.resource) throw new Error('The API sent a port request without a number');
          id = created.resource.id;
          setPortedId({ number: value, id });
        }
        ordered = [id, ...(chosenResources ?? [])];
      }
      const result = await orderAdd(
        account.id,
        offeringId,
        start,
        context.timeZone,
        ordered,
        undefined,
        chosenCharacteristics,
      );
      const warning = result.deviceWarning === null ? '' : ` · ${result.deviceWarning}`;
      if (result.state === 'completed') {
        onOrdered(
          `${chosen.name} ordered · first charge ${formatAmount(first.amount.value)}` +
            ` ${first.amount.unit} in the run of ${first.chargeRunDate}${warning}`,
        );
      } else if (result.state === 'inProgress') {
        // a prepaid order: accepted, and the billing worker completes it (OrderWatch follows)
        onOrdered(
          `${chosen.name} ordered: accepted, the billing worker completes it shortly${warning}`,
          { id: result.id, label: `Order for ${chosen.name}` },
        );
      } else {
        setProblem([result.refusal.problem]);
        setCode(result.refusal.code);
      }
    } catch (error) {
      setProblem([`${explain(error)}. Close and check the subscriber before submitting again.`]);
      setUncertain(true);
    } finally {
      setBusy(false);
    }
  }

  const shortfallCents =
    shortNow && first?.shortfall !== undefined ? toCents(first.shortfall.value) : 0;
  const addLabel = chosen === undefined ? 'Add product' : `Add ${chosen.name}`;

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={draft === undefined ? 'Add product' : 'Edit draft'}
      size="L"
      onClose={onClose}
      ui="ui.subscriber.addProduct"
      context={`${account.customerName} · ${accountLabel(account.id)} · ${account.ratingType}`}
      footer={
        <Footer
          write={{
            busy,
            problem: problem.length ? problem.join(' ') : null,
            unsure: uncertain,
            code,
          }}
          onRefresh={onClose}
          left={
            answeredAt === null ? undefined : (
              <span className="bss-add-product-preview-time">
                Preview from the server ·{' '}
                <time dateTime={answeredAt}>{clockTime(answeredAt, context.timeZone)}</time>
              </span>
            )
          }
        >
          {draft === undefined ? (
            <Button
              data-ui="ui.addProduct.saveDraft"
              disabled={!draftReady || busy || uncertain}
              onClick={() => void saveDraft()}
            >
              Save as draft
            </Button>
          ) : (
            <Button
              data-dialog-primary
              data-ui="ui.addProduct.saveDraftChanges"
              disabled={!draftReady || busy}
              onClick={() => void saveDraft()}
            >
              Save draft
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          {draft === undefined && (
            <Button
              variant={shortNow ? 'secondary' : 'primary'}
              busyLabel="Adding…"
              disabled={!ready || busy || uncertain}
              onClick={() => void confirm()}
            >
              {addLabel}
            </Button>
          )}
          {shortNow && first?.shortfall !== undefined && onTopUp !== undefined && (
            <Button
              variant="primary"
              data-ui="ui.addProduct.topUpShortfall"
              onClick={() => onTopUp(formatCents(shortfallCents).replace(/,/g, ''))}
            >
              Top up {formatCents(shortfallCents)} {first.shortfall.unit}…
            </Button>
          )}
        </Footer>
      }
    >
      <fieldset disabled={busy} className="bss-write-fields bss-add-product">
        <div className="bss-add-product-fields">
          <section>
            <div className="bss-add-product-offering-head">
              <span className="field-label">Offering</span>
              <span className="caption">Active offerings only</span>
              <TextInput
                className="filter"
                width={180}
                placeholder="Filter offerings"
                aria-label="Filter offerings"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            </div>
            <div className="bss-add-product-offerings" role="radiogroup" aria-label="Offering">
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
              {visible.map((offer) => {
                const price = shownPrice(
                  offer.prices,
                  midnightOf(startValid ? start : today, context.timeZone),
                  account.currency,
                );
                return (
                  <div
                    key={offer.id}
                    className="bss-add-product-offering"
                    ref={
                      draft !== undefined && offer.id === draft.offeringId ? scrollToDraft : null
                    }
                    role="radio"
                    aria-checked={offer.id === offeringId}
                    tabIndex={0}
                    onClick={() => setOfferingId(offer.id)}
                    onKeyDown={(event) => {
                      if (event.key === ' ' || event.key === 'Enter') setOfferingId(offer.id);
                    }}
                  >
                    <span className="bss-radio" aria-hidden="true" />
                    <span className="bss-add-product-offering-copy">
                      <span className="bss-name">{offer.name}</span>
                      {offer.code !== null && <span className="bss-code">{offer.code}</span>}
                      <span className="bss-add-product-offering-note">{offer.specification}</span>
                      {activeOfferingIds.has(offer.id) && (
                        <span className="bss-tag">already active</span>
                      )}
                    </span>
                    <span className="bss-add-product-offering-price">
                      {price === null
                        ? '—'
                        : `${formatCents(price.cents)} / ${price.interval ?? 'mo'}`}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>

          <section>
            {requirement.status === 'error' && (
              <ErrorBlock
                title="Failed to load number requirement"
                error={requirement.error}
                onRetry={requirement.retry}
              />
            )}
            <div className="bss-add-product-schedule">
              {needsNumber && (
                <Field label="Number">
                  <SelectField
                    aria-label="Number source"
                    value={porting ? 'port' : 'inventory'}
                    onChange={(e) => setPorting(e.target.value === 'port')}
                  >
                    <option value="inventory">A number from the inventory</option>
                    <option value="port">Port in the subscriber&apos;s number</option>
                  </SelectField>
                </Field>
              )}
              {needsNumber && !porting && <NumberPicker value={numberId} onChange={setNumberId} />}
              {portingNumber && <PortInFields port={port} onChange={setPortField} />}
              {needsImsi && (
                <Field label="SIM">
                  <SelectField
                    aria-label="SIM"
                    value={simChoice}
                    onChange={(e) => setSimChoice(e.target.value as '' | SimType)}
                  >
                    <option value="">A specific SIM in stock</option>
                    <option value="physical">Any physical SIM in stock</option>
                    <option value="esim">Any eSIM in stock</option>
                  </SelectField>
                </Field>
              )}
              {needsImsi && simChoice === '' && (
                <NumberPicker kind="imsi" value={imsiId} onChange={setImsiId} />
              )}
              {asksDevice && (
                <Field
                  label="Device IMEI (optional)"
                  help={
                    imeiError === null
                      ? 'Checked with the network when the order is placed; an incompatible device is refused.'
                      : undefined
                  }
                  error={imeiError ?? undefined}
                >
                  <TextInput
                    aria-label="Device IMEI"
                    data-ui="ui.subscriber.addProductImei"
                    inputMode="numeric"
                    autoComplete="off"
                    maxLength={15}
                    value={imei}
                    onChange={(e) => setImei(e.target.value)}
                  />
                </Field>
              )}
              <Field
                label="Start date"
                help={start === today ? 'Today (business date)' : undefined}
              >
                <DateField
                  aria-label="Start date"
                  min={today}
                  value={start}
                  onChange={(event) => setStart(event.target.value)}
                />
              </Field>
            </div>
            <Note tone="info">
              {!startValid
                ? `the start date cannot be before ${today}`
                : account.ratingType === 'prepaid'
                  ? `Paid from the balance on ${start}`
                  : start === today
                    ? `today · the charge is written by the billing run of ${runOf(start, today)}`
                    : `the charge is written by the billing run of ${start}`}
            </Note>
          </section>

          {shortNow && first?.shortfall !== undefined && (
            <Note
              tone="warning"
              lead={`Shortfall ${formatCents(shortfallCents)} ${first.shortfall.unit}.`}
            >
              The first charge needs {formatAmount((first.required ?? first.amount).value)} and{' '}
              {first.available === undefined ? '—' : formatAmount(first.available.value)} is
              available. Top up first; the product can be added once the balance covers it.
            </Note>
          )}
        </div>
        <aside className="bss-add-product-preview" aria-label="First charge preview">
          {offeringId === null ? (
            <Note tone="info">Select an offering to see the calculation</Note>
          ) : (
            <AddProductPreview preview={preview} account={account} start={start} today={today} />
          )}
        </aside>
      </fieldset>
    </Dialog>
  );
}

const BALANCE_AFTER = 'Balance after unbilled charges and this one';

/** A postpaid account's balance with its unbilled charges and this one: the settlement
 * reads `outstanding − unallocatedCredit = −(balance + unbilled charges)`, so the value is
 * `(unallocatedCredit − outstanding) − charge`, in cents. The preview's own balanceAfter
 * leaves the unbilled charges out. "—" while the settlement loads or fails. */
function BalanceAfter({ accountId, charge }: { accountId: string; charge: FirstCharge['amount'] }) {
  const settlement = useResource(`settlement:${accountId}`, (signal) =>
    loadSettlement(accountId, signal),
  );
  if (settlement.status !== 'ok') return <>—</>;
  const { unallocatedCredit, outstanding } = settlement.data;
  const cents =
    toCents(unallocatedCredit.value) - toCents(outstanding.value) - toCents(charge.value);
  return <Amount cents={cents} currency={charge.unit} />;
}

function AddProductPreview({
  preview,
  account,
  start,
  today,
}: {
  preview: ReturnType<typeof useResource<FirstCharge | null>>;
  account: Account;
  start: string;
  today: string;
}) {
  if (preview.status === 'loading') return <Note tone="info">Calculating…</Note>;
  if (preview.status === 'error') return <Note tone="negative">{explain(preview.error)}</Note>;
  const p = preview.data;
  if (p === null) return <Note tone="info">Select an offering to see the calculation</Note>;

  const now = start === today;
  const prepaid = account.ratingType === 'prepaid' && p.required !== undefined;
  const required = p.required ?? p.amount;
  const shortfallCents = p.shortfall === undefined ? 0 : toCents(p.shortfall.value);
  const short = shortfallCents > 0;
  const price = `${formatAmount(p.price.value)} ${p.price.unit} / ${p.recurringChargePeriod ?? 'mo'}`;
  const details: Parameters<typeof KeyValue>[0]['items'] = [
    ['Price', price],
    [
      'Period',
      <>
        {p.period.start} – {lastDay(p.period.end)}
      </>,
    ],
  ];
  if (prepaid) {
    details.push(['Charging', 'paid in full in advance, no proration']);
  } else {
    details.push(
      [
        'Billable days',
        <>
          {p.billableDays} of {p.periodDays}
          {p.prorated ? ' (prorated)' : ''}
        </>,
      ],
      [
        'Prorated',
        p.prorated
          ? `${formatAmount(p.price.value)} × ${p.billableDays} / ${p.periodDays}`
          : 'full period',
      ],
    );
  }

  return (
    <div
      className="bss-add-product-charge-preview"
      data-ui={prepaid ? 'ui.addProduct.prepaidPreview' : undefined}
    >
      <div className="bss-add-product-charge">
        <span className="bss-add-product-charge-label">First charge</span>
        <span
          className="bss-add-product-charge-amount"
          data-testid={prepaid ? 'prepaid-required' : 'first-charge'}
        >
          {formatAmount((prepaid ? required : p.amount).value)}{' '}
          {(prepaid ? required : p.amount).unit}
        </span>
      </div>
      <KeyValue items={details} />
      {prepaid ? (
        <KeyValue
          items={[
            [
              now ? 'Required now' : `Required on ${start}`,
              `${formatAmount(required.value)} ${required.unit}`,
            ],
            [
              'Available',
              p.available === undefined
                ? '—'
                : `${formatAmount(p.available.value)} ${p.available.unit}`,
            ],
            ...(short && p.shortfall !== undefined
              ? ([
                  [
                    'Shortfall',
                    <span className="bss-add-product-shortfall">
                      {formatCents(shortfallCents)} {p.shortfall.unit}
                    </span>,
                  ],
                ] as Parameters<typeof KeyValue>[0]['items'])
              : []),
            ...(now
              ? ([
                  [
                    'Ledger after',
                    <Amount cents={toCents(p.balanceAfter.value)} currency={p.balanceAfter.unit} />,
                  ],
                ] as Parameters<typeof KeyValue>[0]['items'])
              : []),
          ]}
        />
      ) : (
        <>
          <KeyValue
            items={[
              [
                p.next === null ? 'Then' : `Then from ${p.next.start}`,
                p.next === null
                  ? 'no price after this period'
                  : `${formatAmount(p.next.amount.value)} ${p.next.amount.unit}`,
              ],
              [BALANCE_AFTER, <BalanceAfter accountId={account.id} charge={p.amount} />],
            ]}
          />
          <p className="bss-add-product-preview-copy">
            in the run of {p.chargeRunDate} · on the bill of {p.billDate}
          </p>
          {p.exceedsCreditLimit && p.creditLimit !== null && (
            <Note tone="warning">
              Balance will exceed the credit limit of {formatAmount(p.creditLimit.value)}{' '}
              {p.creditLimit.unit}. Activation is allowed; services may be suspended.
            </Note>
          )}
        </>
      )}
      {prepaid && p.next !== null && (
        <p className="bss-add-product-preview-copy">
          Then {formatAmount(p.next.amount.value)} {p.next.amount.unit} from {p.next.start}.
        </p>
      )}
      {short && !now && (
        <Note tone="info">
          Funds are checked on {start}; nothing is held now. Top up before then.
        </Note>
      )}
      <p className="bss-add-product-preview-copy">
        {prepaid
          ? now
            ? 'Submitting holds the full interval with its taxes; the billing worker charges it and renews at the paid-through date while funds last.'
            : `On ${start} the billing worker takes the interval from the funds available then.`
          : `Prorated by days until the account’s next billing day (day ${account.cycleDay}). Full price is charged every cycle after that. An order completed today is charged by tomorrow’s billing run at the earliest.`}
      </p>
    </div>
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
          <dt>{BALANCE_AFTER}</dt>
          <dd>
            <BalanceAfter accountId={account.id} charge={p.amount} />
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
// frontend.md, docs/ui.md). The figures are the backend's: required,
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
  // an offering change holds the new interval when the order is placed, whatever its date
  const change = p.credit !== undefined;
  const now = start === today || change;
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
          <dt>{change ? 'Held now' : now ? 'To pay now' : `To pay on ${start}`}</dt>
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
          {p.credit !== undefined && (
            <>
              <dt>Credit for unused days</dt>
              <dd>
                {formatAmount(p.credit.value)} {p.credit.unit}
              </dd>
            </>
          )}
          {now && (
            <>
              <dt>{change ? 'Balance after' : 'Ledger after'}</dt>
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
        {change
          ? `Submitting holds the new interval now; on ${start} the billing worker credits the unused days and charges it.`
          : now
            ? 'Submitting holds the full interval with its taxes; the billing worker charges it and renews at the paid-through date while funds last.'
            : `On ${start} the billing worker takes the interval from the funds available then.`}
      </div>
    </div>
  );
}

/** The losing carrier's details for a port-in. The PIN is the carrier's, not ours: it is sent
 * once and the server drops it when the carrier has accepted the request. */
function PortInFields({
  port,
  onChange,
}: {
  port: PortInData;
  onChange: (key: keyof PortInData, value: string) => void;
}) {
  return (
    <>
      <Field label="Number to port">
        <TextInput
          mono
          aria-label="Number to port"
          placeholder="15551234567"
          value={port.number}
          onChange={(e) => onChange('number', e.target.value)}
        />
      </Field>
      <Field label="Carrier account number">
        <TextInput
          value={port.carrierAccountNumber}
          onChange={(e) => onChange('carrierAccountNumber', e.target.value)}
        />
      </Field>
      <Field label="Number Transfer PIN">
        <TextInput
          mono
          autoComplete="off"
          value={port.transferPin}
          onChange={(e) => onChange('transferPin', e.target.value)}
        />
      </Field>
      <Field label="Billing ZIP">
        <TextInput
          mono
          value={port.billingZip}
          onChange={(e) => onChange('billingZip', e.target.value)}
        />
      </Field>
      <Field label="First name">
        <TextInput value={port.firstName} onChange={(e) => onChange('firstName', e.target.value)} />
      </Field>
      <Field label="Last name">
        <TextInput value={port.lastName} onChange={(e) => onChange('lastName', e.target.value)} />
      </Field>
      <Note tone="info">
        The port request is sent to the carrier by the billing worker; the line is created and
        charged when the port completes. Saving a draft is not available for a port-in.
      </Note>
    </>
  );
}
