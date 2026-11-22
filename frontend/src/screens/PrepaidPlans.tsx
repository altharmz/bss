import { MultiLineDiscount, multiLineLabel } from './MultiLine';
import { useRef, useState } from 'react';
import {
  KIND_LABEL,
  passLength,
  loadAddOnOfferings,
  loadFirstCharge,
  orderAddOn,
  type Account,
  type AccountProduct,
  type FirstCharge,
} from '../api/bss';
import { explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { shortId } from '../format/ids';
import { formatCents, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Note } from '../ui/Note';
import { Button, Dialog, submitDialogPrimary } from '../ui/kit';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { Footer } from './FinancialForms';
import { uncertain } from './submissionKey';
import { TaxLines } from './TaxLines';

// The prepaid plan features of the subscriber's product row (docs/decisions/prepaid.md,
// "Plans, renewal, add-ons and fees"): the plan facts, the renewal history and Buy add-on.
// The server decides every rule and quotes every figure; nothing is computed here.

const FUNDING: Record<NonNullable<AccountProduct['fundingSource']>, string> = {
  balance: 'Renews from the balance',
  balanceThenCard: 'Renews from the balance, then the default card',
};
const HISTORY_KIND: Record<string, string> = {
  purchase: 'Purchase',
  renewal: 'Renewal',
  offeringChange: 'Offering change',
};

/** A prepaid plan product: it can take add-on packs and has a renewal. */
export function isPlan(product: AccountProduct): boolean {
  return product.offeringKind === 'plan';
}

/** Whether Buy add-on is offered: a plan with a paid period running (the server decides). */
export function canBuyAddOn(product: AccountProduct, today: string): boolean {
  return (
    isPlan(product) &&
    product.status === 'active' &&
    product.paidThrough != null &&
    product.paidThrough > today
  );
}

/** What the row says about a prepaid product besides its status: kind, how it renews, and
 * for an add-on its plan and end date. */
export function PlanFacts({ product }: { product: AccountProduct }) {
  if (product.offeringKind === undefined) return null;
  return (
    <div className="bss-secondary" data-ui="ui.subscriber.planFacts">
      {KIND_LABEL[product.offeringKind]}
      {product.offeringKind === 'plan' && product.fundingSource !== undefined && (
        <>
          {' · '}
          {FUNDING[product.fundingSource]}
          {product.nextChargeDate != null && ` on ${product.nextChargeDate}`}
        </>
      )}
      {product.offeringKind === 'addOn' && (
        <>
          {product.passDays != null && ` · ${passLength(product.passDays)} pass`}
          {product.parentProduct != null && ` · plan PRD-${shortId(product.parentProduct.id)}`}
          {product.terminationDate !== null && ` · ends ${product.terminationDate}`}
        </>
      )}
    </div>
  );
}

/** A plan's live passes (issue 383): its add-ons with a pass length that have not ended,
 * each with its end date (exclusive, as the server stores it). Nothing without one. */
export function LivePasses({
  plan,
  products,
  today,
}: {
  plan: AccountProduct;
  products: AccountProduct[];
  today: string;
}) {
  const passes = products.filter(
    (p) =>
      p.offeringKind === 'addOn' &&
      p.passDays != null &&
      p.parentProduct?.id === plan.id &&
      p.startDate <= today &&
      (p.terminationDate === null || p.terminationDate > today) &&
      p.status !== 'terminated',
  );
  if (!isPlan(plan) || passes.length === 0) return null;
  return (
    <div className="bss-secondary" data-ui="ui.subscriber.passes">
      Live passes:{' '}
      {passes.map((p, index) => (
        <span key={p.id}>
          {index > 0 && ', '}
          {p.productOffering.name} ({passLength(p.passDays ?? 0)})
          {p.terminationDate !== null && ` ends ${p.terminationDate}`}
        </span>
      ))}
    </div>
  );
}

/** The product's funded intervals, newest first, behind a toggle. */
export function RenewalHistory({ product }: { product: AccountProduct }) {
  const [open, setOpen] = useState(false);
  const history = product.renewalHistory ?? [];
  if (history.length === 0) return null;
  return (
    <div data-ui="ui.subscriber.renewalHistory">
      <button type="button" className="bss-link" onClick={() => setOpen(!open)}>
        {open ? 'Hide' : 'Show'} renewal history ({history.length})
      </button>
      {open && (
        <table className="bss-secondary" aria-label="Renewal history">
          <thead>
            <tr>
              <th>Period</th>
              <th>Kind</th>
              <th>Amount</th>
              <th>Tax</th>
              <th>Funded by</th>
              <th>Bill</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h) => [
              <tr key={h.authorizedAt}>
                <td className="num">
                  {h.period.start} – {h.period.end}
                </td>
                <td>{HISTORY_KIND[h.kind] ?? h.kind}</td>
                <td className="num">
                  {formatCents(toCents(h.amount.value))} {h.amount.unit}
                  {h.discount != null && (
                    <>
                      {' '}
                      <MultiLineDiscount text={multiLineLabel(h.discount.amount)} />
                    </>
                  )}
                </td>
                <td className="num">{formatCents(toCents(h.tax.value))}</td>
                <td>{h.fundedBy === 'card' ? 'Card' : 'Balance'}</td>
                <td className="num">{h.bill === null ? '—' : `BILL-${shortId(h.bill.id)}`}</td>
              </tr>,
              h.appliedTax.length > 0 && (
                <tr key={`${h.authorizedAt}:taxes`}>
                  <td colSpan={6}>
                    <TaxLines lines={h.appliedTax} />
                  </td>
                </tr>
              ),
            ])}
          </tbody>
        </table>
      )}
    </div>
  );
}

const OFFER_COLUMNS = [
  { key: 'radio', label: '', width: '28px' },
  { key: 'name', label: '', width: '1fr' },
];

/** Buy an add-on pack for a plan: the add-on offerings, the server's quote (fees and taxes)
 * and the order. An add-on starts today and ends with the plan's paid period. */
export function BuyAddOnDialog({
  account,
  product,
  onClose,
  onDone,
  onRefresh,
  onTopUp,
}: {
  account: Account;
  product: AccountProduct;
  onClose: () => void;
  onDone: (message: string, accepted?: PendingOrder) => void;
  onRefresh: () => void;
  onTopUp: (amount: string) => void;
}) {
  const context = useSystemContext();
  const today = context.businessDate;
  const [offeringId, setOfferingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [code, setCode] = useState<string | undefined>();
  const [unsure, setUnsure] = useState(false);
  const saving = useRef(false);
  const name = product.productOffering.name;
  const offerings = useResource('addOnOfferings', loadAddOnOfferings);
  const preview = useResource<FirstCharge | null>(
    offeringId === null
      ? 'addOnQuote:none'
      : `addOnQuote:${account.id}:${product.id}:${offeringId}:${today}`,
    (signal) =>
      offeringId === null
        ? Promise.resolve(null)
        : loadFirstCharge(account.id, offeringId, today, signal, undefined, product.id),
  );
  const chosen = offerings.status === 'ok' ? offerings.data.find((o) => o.id === offeringId) : null;
  const short =
    preview.status === 'ok' && preview.data?.shortfall !== undefined
      ? toCents(preview.data.shortfall.value)
      : 0;

  async function submit() {
    if (offeringId === null || chosen == null || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    setCode(undefined);
    setUnsure(false);
    try {
      const result = await orderAddOn(account.id, offeringId, product.id, today, context.timeZone);
      if (result.state === 'completed') {
        onDone(`${chosen.name} added to ${name}`);
      } else if (result.state === 'inProgress') {
        onDone(`${chosen.name} accepted for ${name}, the billing worker completes it`, {
          id: result.id,
          label: `Add-on ${chosen.name} for ${name}`,
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
      title={`Buy add-on: ${name}`}
      size="L"
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui="ui.subscriber.buyAddOn"
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
            disabled={offeringId === null || busy}
            onClick={() => void submit()}
          >
            Buy add-on
          </Button>
        </Footer>
      }
    >
      <fieldset disabled={busy} className="bss-write-fields bss-modal-body">
        <section>
          <div className="bss-step-head">1. Add-on</div>
          <div className="bss-offer-list" role="radiogroup" aria-label="Add-on">
            {offerings.status === 'loading' && <SkeletonRows columns={OFFER_COLUMNS} rows={3} />}
            {offerings.status === 'error' && (
              <ErrorBlock
                title="Failed to load add-ons"
                error={offerings.error}
                onRetry={offerings.retry}
              />
            )}
            {offerings.status === 'ok' && offerings.data.length === 0 && (
              <div className="bss-state">
                <div className="text">No add-on offering is on sale.</div>
              </div>
            )}
            {offerings.status === 'ok' &&
              offerings.data.map((offer) => (
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
                </div>
              ))}
          </div>
        </section>
        <section>
          <div className="bss-step-head">2. Price with taxes and fees</div>
          <div className="bss-preview" data-ui="ui.subscriber.buyAddOn.quote">
            {preview.status === 'loading' && <Note tone="info">Calculating…</Note>}
            {preview.status === 'error' && <Note tone="negative">{explain(preview.error)}</Note>}
            {preview.status === 'ok' && preview.data === null && (
              <Note tone="info">Select an add-on to see the price</Note>
            )}
            {preview.status === 'ok' && preview.data !== null && (
              <AddOnQuote quote={preview.data} />
            )}
            {short > 0 && preview.status === 'ok' && preview.data !== null && (
              <Note tone="warning">
                Insufficient funds: top up at least {formatCents(short)} {preview.data.amount.unit}.
                The order is refused until the account has the funds.{' '}
                <Button
                  data-ui="ui.subscriber.buyAddOn.topUp"
                  onClick={() => onTopUp(formatCents(short).replace(/,/g, ''))}
                >
                  Top up {formatCents(short)}
                </Button>
              </Note>
            )}
          </div>
          <p className="bss-secondary">
            The pack starts today and ends with the plan's paid period
            {product.paidThrough != null ? ` (${product.paidThrough})` : ''}, a pass after its
            number of days if that comes first; it is never renewed.
          </p>
        </section>
      </fieldset>
    </Dialog>
  );
}

function AddOnQuote({ quote }: { quote: FirstCharge }) {
  const fees = quote.fees ?? [];
  const addOn = quote.addOn;
  return (
    <dl className="bss-preview-grid">
      {addOn !== undefined && (
        <div data-ui="ui.subscriber.buyAddOn.passEnd">
          <dt>{addOn.passDays === null ? 'Lasts' : 'Pass length'}</dt>
          <dd>
            {addOn.passDays === null ? 'the plan period' : passLength(addOn.passDays)} · ends{' '}
            {addOn.endDate}
          </dd>
        </div>
      )}
      {fees.map((f) => (
        <div key={f.name}>
          <dt>{f.name}</dt>
          <dd>
            {formatCents(toCents(f.amount.value))} + tax {formatCents(toCents(f.tax.value))} ={' '}
            {formatCents(toCents(f.gross.value))} {f.gross.unit}
          </dd>
        </div>
      ))}
      <div>
        <dt>To pay now</dt>
        <dd>
          <span className="first" data-testid="addon-required">
            {formatCents(toCents((quote.required ?? quote.amount).value))} {quote.amount.unit}
          </span>
        </dd>
      </div>
      {quote.available !== undefined && (
        <div>
          <dt>Available</dt>
          <dd>
            {formatCents(toCents(quote.available.value))} {quote.available.unit}
          </dd>
        </div>
      )}
      <div>
        <dt>Balance after</dt>
        <dd>
          <Amount cents={toCents(quote.balanceAfter.value)} currency={quote.balanceAfter.unit} />
        </dd>
      </div>
    </dl>
  );
}
