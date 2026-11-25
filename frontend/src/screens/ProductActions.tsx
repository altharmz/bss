import { Footer } from './FinancialForms';
import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useRef, useState } from 'react';
import {
  loadFirstCharge,
  loadOfferingDetail,
  loadSellableOfferings,
  midnightOf,
  orderOfferingChange,
  orderProductChange,
  orderDelete,
  shownPrice,
  type Account,
  type AccountProduct,
  type FirstCharge,
  type ProductChange,
} from '../api/bss';
import { explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { addDays, isIsoDate } from '../format/dates';
import { formatCents, toCents } from '../format/money';
import { Button, Menu, Dialog, DateField, TextInput, type MenuItem } from '../ui/kit';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { Preview } from './AddProduct';
import { offersE911Edit } from './E911Address';
import { canBuyAddOn } from './PrepaidPlans';
import { uncertain } from './submissionKey';

// Product actions of the subscriber card: suspend, resume and end on a date, as TMF622 orders
// (docs/ui.md, decisions/frontend.md). The server decides every rule;
// the menu hides an item only when the row's own data makes the refusal certain.

/** A menu item: a status or end change, or the offering change of slice 5. */
export type ProductAction = ProductChange | 'offering';

export const CHANGE_UI: Record<ProductAction, string> = {
  suspend: 'ui.subscriber.suspend',
  resume: 'ui.subscriber.resume',
  offering: 'ui.subscriber.changeOffering',
  end: 'ui.subscriber.endOnDate',
};

const TITLE: Record<ProductAction, string> = {
  suspend: 'Suspend',
  resume: 'Resume',
  offering: 'Change offering',
  end: 'End on a date',
};

/** The change items the row offers, from what it already shows. */
export function offeredChanges(
  product: AccountProduct,
  ratingType?: Account['ratingType'],
): ProductAction[] {
  const statusPending =
    !!product.undoableStatusChange || product.pendingChanges.some((c) => c.kind === 'status');
  const items: ProductAction[] = [];
  if (product.status === 'active' && !statusPending) items.push('suspend');
  if (
    product.status === 'suspended' &&
    product.suspensionCause !== 'funding' &&
    product.suspensionCause !== 'account' &&
    !statusPending
  ) {
    items.push('resume');
  }
  const ended = product.terminationDate !== null || product.status === 'pendingTerminate';
  if (
    (product.status === 'active' || product.status === 'suspended') &&
    !ended &&
    !product.pendingChanges.some((c) => c.kind === 'offering') &&
    (ratingType !== 'prepaid' || product.paidThrough != null)
  ) {
    items.push('offering');
  }
  if (product.terminationDate === null && product.status !== 'pendingTerminate') items.push('end');
  return items;
}

/** What the product menu's own items open; an item without its handler is left out (the
 * product page offers no add-on purchase or top-up: they need the card's balance). */
export interface ProductMenuHandlers {
  onNumber: () => void;
  onTransferPin: () => void;
  onDevice: () => void;
  onE911?: () => void;
  onBuyAddOn?: () => void;
  onTopUp?: (amount: string) => void;
  onDeactivate: () => void;
}

/** The items of a product's menu besides the status and offering changes: the same on the
 * billing account card's row and on the product page (#458). */
export function productMenuExtra(
  product: AccountProduct,
  ratingType: Account['ratingType'],
  today: string,
  handlers: ProductMenuHandlers,
): MenuItem[] {
  const { onE911, onBuyAddOn, onTopUp } = handlers;
  const prepaid = ratingType === 'prepaid';
  const fundingWait =
    prepaid &&
    product.status === 'suspended' &&
    product.suspensionCause === 'funding' &&
    product.needs_topup
      ? product.needs_topup
      : null;
  return [
    { label: 'Subscriber number', onClick: handlers.onNumber },
    ...(product.status === 'active' || product.status === 'suspended'
      ? [
          {
            label: 'Number Transfer PIN…',
            ui: 'ui.subscriber.transferPinItem',
            onClick: handlers.onTransferPin,
          },
        ]
      : []),
    ...(prepaid
      ? [{ label: 'Device…', ui: 'ui.subscriber.deviceItem', onClick: handlers.onDevice }]
      : []),
    ...(onE911 !== undefined && offersE911Edit(product, prepaid)
      ? [{ label: 'Edit E911 address…', ui: 'ui.subscriber.e911.edit', onClick: onE911 }]
      : []),
    ...(onBuyAddOn !== undefined && canBuyAddOn(product, today)
      ? [{ label: 'Buy add-on…', ui: 'ui.subscriber.buyAddOnItem', onClick: onBuyAddOn }]
      : []),
    ...(onTopUp !== undefined && fundingWait !== null
      ? [
          {
            label: 'Top up',
            ui: 'ui.subscriber.topUpProduct',
            onClick: () => onTopUp(formatCents(toCents(fundingWait.shortfall)).replace(/,/g, '')),
          },
        ]
      : []),
    {
      label: 'Deactivate',
      tone: 'negative' as const,
      ui: 'ui.subscriber.deactivate',
      onClick: handlers.onDeactivate,
    },
  ];
}

export function ProductActionsMenu({
  items,
  onPick,
  undoable,
  onUndo,
  busy,
  extra = [],
}: {
  extra?: MenuItem[];
  undoable?: AccountProduct['undoableStatusChange'];
  onUndo?: () => void;
  busy?: boolean;
  items: ProductAction[];
  onPick: (change: ProductAction) => void;
}) {
  if (items.length === 0 && !undoable && extra.length === 0) return null;
  return (
    <Menu
      label="Actions"
      ui="ui.subscriber.productActions"
      items={[
        ...(undoable
          ? [
              {
                label: undoable.status === 'suspended' ? 'Undo suspension' : 'Undo resumption',
                ui: 'ui.subscriber.undoStatusChange',
                disabled: !!busy,
                onClick: () => onUndo?.(),
              },
            ]
          : []),
        ...extra,
        ...items.map((item) => ({
          label: `${TITLE[item]}…`,
          ui: CHANGE_UI[item],
          onClick: () => onPick(item),
        })),
      ]}
    />
  );
}

export function ProductChangeDialog({
  change,
  immediateEnd = false,
  account,
  product,
  onClose,
  onDone,
  onRefresh,
  onTopUp,
}: {
  onTopUp?: (amount: string) => void;
  change: ProductChange;
  immediateEnd?: boolean;
  account: Account;
  product: AccountProduct;
  onClose: () => void;
  onDone: (message: string, accepted?: PendingOrder) => void;
  onRefresh: () => void;
}) {
  const context = useSystemContext();
  const today = context.businessDate;
  const prepaid = account.ratingType === 'prepaid';
  const [day, setDay] = useState(today);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [code, setCode] = useState<string | undefined>();
  const [unsure, setUnsure] = useState(false);
  const saving = useRef(false);
  const name = product.productOffering.name;
  const valid = isIsoDate(day);
  const expired = prepaid && product.paidThrough != null && product.paidThrough <= day;

  const chargesNow = change === 'resume' && expired && day <= today;
  const preview = useResource<FirstCharge | null>(
    `resume:${product.id}:${day}:${chargesNow}`,
    (signal) =>
      chargesNow && valid
        ? loadFirstCharge(account.id, product.productOffering.id, today, signal)
        : Promise.resolve(null),
  );
  const short =
    preview.status === 'ok' &&
    preview.data?.shortfall !== undefined &&
    toCents(preview.data.shortfall.value) > 0;
  const ready =
    valid && (!chargesNow || (preview.status === 'ok' && preview.data !== null && !short));

  const explanation =
    change === 'end'
      ? prepaid
        ? `Unused paid days from ${day} are credited to the balance.`
        : `The product is billed up to the day before ${day}.`
      : prepaid
        ? change === 'resume' && expired
          ? 'The paid interval has ended: resuming starts a new interval paid from the balance.'
          : 'Billing does not change while a product is suspended: it is charged by its dates. Paid days are not extended by a suspension.'
        : 'Billing does not change while a product is suspended: it is charged by its dates.';

  async function submit() {
    if (!ready || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    setCode(undefined);
    setUnsure(false);
    try {
      const result =
        immediateEnd && change === 'end' && day === today
          ? await orderDelete(account.id, product.id)
          : await orderProductChange(account.id, product.id, change, day, context.timeZone);
      if (result.state === 'completed') {
        onDone(
          immediateEnd && change === 'end' && day === today
            ? product.startDate > today
              ? `${name} cancelled before its start`
              : `${name} deactivated as of ${today}`
            : `${name}: ${TITLE[change].toLowerCase()} recorded for ${day}`,
        );
      } else if (result.state === 'inProgress') {
        onDone(
          immediateEnd && change === 'end' && day === today
            ? `${name}: deactivation accepted, the billing worker completes it shortly`
            : `${name}: ${TITLE[change].toLowerCase()} accepted, the billing worker completes it`,
          {
            id: result.id,
            label:
              immediateEnd && change === 'end' && day === today
                ? `Deactivation of ${name}`
                : `${TITLE[change]} of ${name}`,
          },
        );
      } else {
        setProblem(result.refusal.problem);
        setCode(result.refusal.code);
      }
    } catch (error) {
      if (uncertain(error)) {
        setUnsure(true);
        setProblem(`${explain(error)}. Unsure whether it was saved: Refresh to check.`);
      } else {
        setProblem(explain(error));
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={change === 'end' ? undefined : submitDialogPrimary}
      title={`${TITLE[change]}: ${name}`}
      size={change === 'end' ? 'S' : 'M'}
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui="ui.subscriber.productChange"
      footer={
        <Footer
          write={{ busy, problem, unsure, code }}
          onRefresh={() => {
            onClose();
            onRefresh();
          }}
        >
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!ready || busy} onClick={() => void submit()}>
            {TITLE[change]}
          </Button>
        </Footer>
      }
    >
      <fieldset disabled={busy} className="bss-write-fields bss-modal-body">
        <section>
          <div className="bss-step-head">{change === 'end' ? 'End on' : 'Date'}</div>
          <DateField
            aria-label="Date"
            placeholder="YYYY-MM-DD"
            value={day}
            onChange={(event) => setDay(event.target.value)}
          />
          {!valid && <Note tone="negative">Enter a date as YYYY-MM-DD.</Note>}
        </section>
        <p className="bss-secondary">{explanation}</p>
        {chargesNow && (
          <div data-ui="ui.subscriber.resumePreview">
            <Preview
              preview={preview}
              account={account}
              start={today}
              today={today}
              onTopUp={onTopUp}
            />
          </div>
        )}
      </fieldset>
    </Dialog>
  );
}

export function ChangeOfferingDialog({
  account,
  product,
  onClose,
  onDone,
  onRefresh,
}: {
  account: Account;
  product: AccountProduct;
  onClose: () => void;
  onDone: (message: string, accepted?: PendingOrder) => void;
  onRefresh: () => void;
}) {
  const context = useSystemContext();
  const today = context.businessDate;
  const prepaid = account.ratingType === 'prepaid';
  // prepaid: any date from today to the paid-through date (default: the renewal there; a
  // date before it credits the unused days); an expired product changes today. Postpaid:
  // the cycle boundary by default, any date from today
  const boundary =
    product.paidThrough != null && product.paidThrough > today ? product.paidThrough : today;
  const [day, setDay] = useState(
    prepaid
      ? boundary
      : product.nextChargeDate != null && product.nextChargeDate >= today
        ? product.nextChargeDate
        : today,
  );
  const [offeringId, setOfferingId] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [code, setCode] = useState<string | undefined>();
  const [unsure, setUnsure] = useState(false);
  const saving = useRef(false);
  const name = product.productOffering.name;
  const valid = isIsoDate(day) && day >= today && (!prepaid || day <= boundary);

  const offerings = useResource('offerings', (signal) =>
    loadSellableOfferings(account.currency, signal),
  );
  const currentOffering = useResource(`offering:${product.productOffering.id}`, (signal) =>
    loadOfferingDetail(product.productOffering.id, signal),
  );
  const previewKey = offeringId !== null && valid ? `change:${offeringId}|${day}` : 'none';
  const preview = useResource<FirstCharge | null>(previewKey, (signal) =>
    offeringId !== null && valid
      ? loadFirstCharge(account.id, offeringId, day, signal, prepaid ? product.id : undefined)
      : Promise.resolve(null),
  );
  const q = filter.trim().toLowerCase();
  const priceAt = midnightOf(valid ? day : today, context.timeZone);
  const visible =
    offerings.status === 'ok'
      ? offerings.data.filter(
          (o) =>
            o.id !== product.productOffering.id &&
            currentOffering.status === 'ok' &&
            o.specificationId === currentOffering.data.specification?.id &&
            shownPrice(o.prices, priceAt, account.currency) !== null &&
            (!q || o.name.toLowerCase().includes(q) || (o.code ?? '').toLowerCase().includes(q)),
        )
      : [];
  const chosen = visible.find((o) => o.id === offeringId);

  async function submit() {
    if (!valid || offeringId === null || chosen === undefined || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    setCode(undefined);
    setUnsure(false);
    try {
      const result = await orderOfferingChange(
        account.id,
        product.id,
        offeringId,
        day,
        context.timeZone,
      );
      if (result.state === 'completed') {
        onDone(`${name}: change to ${chosen.name} recorded for ${day}`);
      } else if (result.state === 'inProgress') {
        onDone(`${name}: change to ${chosen.name} accepted, the billing worker completes it`, {
          id: result.id,
          label: `Change of ${name} to ${chosen.name}`,
        });
      } else {
        setProblem(result.refusal.problem);
        setCode(result.refusal.code);
      }
    } catch (error) {
      if (uncertain(error)) {
        setUnsure(true);
        setProblem(`${explain(error)}. Unsure whether it was saved: Refresh to check.`);
      } else {
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
      title={`Change offering: ${name}`}
      size="L"
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui="ui.subscriber.offeringChange"
      footer={
        <Footer
          write={{ busy, problem, unsure, code }}
          onRefresh={() => {
            onClose();
            onRefresh();
          }}
        >
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!valid || offeringId === null || busy}
            onClick={() => void submit()}
          >
            Change offering
          </Button>
        </Footer>
      }
    >
      <fieldset disabled={busy} className="bss-write-fields bss-modal-body">
        <section>
          <div className="bss-step-head">
            1. New offering
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
            {(offerings.status === 'loading' || currentOffering.status === 'loading') && (
              <SkeletonRows columns={OFFER_COLUMNS} rows={3} />
            )}
            {currentOffering.status === 'error' && (
              <ErrorBlock
                title="Failed to load the current offering"
                error={currentOffering.error}
                onRetry={currentOffering.retry}
              />
            )}
            {offerings.status === 'error' && (
              <ErrorBlock
                title="Failed to load offerings"
                error={offerings.error}
                onRetry={offerings.retry}
              />
            )}
            {offerings.status === 'ok' &&
              currentOffering.status === 'ok' &&
              visible.length === 0 && (
                <div className="bss-state">
                  <div className="text">No other offering uses this product's specification.</div>
                </div>
              )}
            {visible.map((offer) => {
              const price = shownPrice(offer.prices, priceAt, account.currency);
              return (
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
                    {offer.code !== null && <div className="bss-code">{offer.code}</div>}
                  </span>
                  <span>{offer.specification}</span>
                  <span className="num num-right">
                    {formatCents(price?.cents ?? 0)} / {price?.interval ?? 'mo'}
                  </span>
                </div>
              );
            })}
          </div>
        </section>
        <section>
          <div className="bss-step-head">2. Date</div>
          <TextInput
            mono
            width={180}
            type="date"
            aria-label="Date"
            min={today}
            max={prepaid ? boundary : undefined}
            value={day}
            onChange={(event) => setDay(event.target.value)}
          />
          {!valid && (
            <Note tone="negative">
              Enter a date from {today}
              {prepaid ? ` to ${boundary}` : ''}.
            </Note>
          )}
          <p className="bss-secondary">
            {prepaid
              ? day === boundary
                ? `The change applies at the next renewal (${day}) and only when the balance covers the new offering's interval; until then the current offering stays.`
                : `The unused days of the paid interval are credited on ${day}, and the new offering's first interval is held from the balance now.`
              : `The old offering is billed until ${addDays(day, -1)}, the new one from ${day}; a charge already billed for the period is adjusted by a prorated refund.`}
          </p>
        </section>
        <section>
          <div className="bss-step-head">
            3.{' '}
            {prepaid
              ? day === boundary
                ? `Renewal on ${day}`
                : `Change on ${day}`
              : `New offering from ${day}`}
          </div>
          <div className="bss-preview">
            {offeringId === null ? (
              <Note tone="info">Select an offering to see the calculation</Note>
            ) : (
              <Preview
                preview={preview}
                account={account}
                start={day}
                today={today}
                onTopUp={undefined}
              />
            )}
          </div>
        </section>
      </fieldset>
    </Dialog>
  );
}

const OFFER_COLUMNS = [
  { key: 'radio', label: '', width: '28px' },
  { key: 'name', label: '', width: '1fr' },
  { key: 'spec', label: '', width: '170px' },
  { key: 'price', label: '', width: '150px', align: 'right' as const, mono: true },
];
