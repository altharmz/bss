import { useState } from 'react';
import {
  deletePrice,
  endPrice,
  loadCategories,
  loadPriceDetail,
  offeringChanges,
  offeringEditFields,
  patchOffering,
  patchPrice,
  priceChanges,
  type OfferingDetail,
  type OfferingEditFields,
  type PriceDetail,
  type PriceEditFields,
  type Specification,
} from '../api/bss';
import { ApiError, explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { addDays, businessDate } from '../format/dates';
import { formatCents, parseAmount } from '../format/money';
import { Button, Modal, TextInput } from '../ui/kit';

// Edit offering, and the price dialog (edit, end, delete) opened from the offering
// detail. The server decides what is allowed: a refusal is shown with the entered values
// kept. Design: docs/next-slices-design.md section 12.

const CODE = /^[A-Z0-9][A-Z0-9_-]{0,31}$/;

export function OfferingEditModal({
  detail,
  specifications,
  onClose,
  onSaved,
}: {
  detail: OfferingDetail;
  specifications: Specification[];
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const context = useSystemContext();
  const categories = useResource('categories', loadCategories);
  const [form, setForm] = useState<OfferingEditFields>(
    offeringEditFields(detail, context.timeZone),
  );
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const set = <K extends keyof OfferingEditFields>(key: K, value: OfferingEditFields[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const changes = offeringChanges(detail, form, context.timeZone);
  const code = form.code.trim().toUpperCase();
  let error: string | null = null;
  if (form.name.trim() === '') error = 'Name is required';
  else if (code !== '' && !CODE.test(code))
    error = 'Code may use A-Z, 0-9, "-" and "_" (up to 32 characters)';
  else if (form.validFrom && form.validTo && form.validTo < form.validFrom)
    error = 'End date is before start date';
  const ready = error === null && changes !== null;

  async function save() {
    if (changes === null || !ready) return;
    setBusy(true);
    setProblem(null);
    try {
      await patchOffering(detail.id, changes);
      onSaved(`Offering ${code || form.name.trim()} saved`);
    } catch (failure) {
      setProblem(explain(failure));
    } finally {
      setBusy(false);
    }
  }

  const message = problem ?? error;
  const toggle = (id: string) =>
    set(
      'categoryIds',
      form.categoryIds.includes(id)
        ? form.categoryIds.filter((c) => c !== id)
        : [...form.categoryIds, id],
    );
  return (
    <Modal
      title={`Edit ${detail.name}`}
      width={560}
      onClose={onClose}
      ui="ui.catalog.editOffering"
      footer={
        <>
          {message !== null && (
            <div className="footer-push bss-inline-error" role="alert">
              {message}
            </div>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready || busy} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <label>
          Name
          <TextInput value={form.name} onChange={(e) => set('name', e.target.value)} />
        </label>
        <label>
          Description
          <TextInput
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
          />
        </label>
        <label>
          Code
          <TextInput
            className="uppercase"
            mono
            value={form.code}

            onChange={(e) => set('code', e.target.value)}
          />
        </label>
        <label>
          Specification
          <select
            className="bss-input"
            value={form.specificationId}
            onChange={(e) => set('specificationId', e.target.value)}
          >
            {form.specificationId === '' && <option value="">—</option>}
            {specifications.map((spec) => (
              <option key={spec.id} value={spec.id}>
                {spec.name}
              </option>
            ))}
          </select>
          <span className="bss-secondary">
            The server refuses it once the offering was ordered.
          </span>
        </label>
        <label>
          <span>
            <input
              type="checkbox"
              checked={form.isSellable}
              onChange={(e) => set('isSellable', e.target.checked)}
            />{' '}
            Sellable
          </span>
        </label>
        <div className="bss-form-pair">
          <label>
            Valid from
            <TextInput
              mono
              type="date"
              value={form.validFrom}
              onChange={(e) => set('validFrom', e.target.value)}
            />
          </label>
          <label>
            Valid to (empty = open-ended)
            <TextInput
              mono
              type="date"
              value={form.validTo}
              onChange={(e) => set('validTo', e.target.value)}
            />
          </label>
        </div>
        <fieldset aria-label="Categories">
          <legend>Categories</legend>
          {categories.status === 'loading' && <span className="bss-hint">Loading…</span>}
          {categories.status === 'error' && (
            <span className="bss-inline-error">{categories.error.message}</span>
          )}
          {categories.status === 'ok' && categories.data.length === 0 && (
            <span className="bss-hint">No categories yet.</span>
          )}
          {categories.status === 'ok' &&
            categories.data.map((c) => (
              <div key={c.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={form.categoryIds.includes(c.id)}
                    onChange={() => toggle(c.id)}
                  />{' '}
                  {c.name}
                </label>
              </div>
            ))}
        </fieldset>
      </div>
    </Modal>
  );
}

// --- a price of the offering ---

export function PriceModal({
  priceId,
  onClose,
  onChanged,
}: {
  priceId: string;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const price = useResource(`price:${priceId}`, (signal) => loadPriceDetail(priceId, signal));
  if (price.status !== 'ok') {
    return (
      <Modal
        title="Price"
        width={520}
        onClose={onClose}
        ui="ui.catalog.priceDetail"
        footer={<Button onClick={onClose}>Close</Button>}
      >
        <div className="bss-modal-body">
          {price.status === 'loading' ? (
            <span className="bss-hint">Loading…</span>
          ) : (
            <div className="bss-inline-error" role="alert">
              {price.error.message}
            </div>
          )}
        </div>
      </Modal>
    );
  }
  return <PriceBody price={price.data} onClose={onClose} onChanged={onChanged} />;
}

function PriceBody({
  price,
  onClose,
  onChanged,
}: {
  price: PriceDetail;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const context = useSystemContext();
  const tomorrow = addDays(context.businessDate, 1);
  const [form, setForm] = useState<PriceEditFields>({
    name: price.name,
    description: price.description ?? '',
    amount: price.cents === null ? '' : (price.cents / 100).toFixed(2),
  });
  const [endDay, setEndDay] = useState(tomorrow);
  const [ending, setEnding] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [hint, setHint] = useState(false);
  const set = (key: keyof PriceEditFields) => (value: string) =>
    setForm((f) => ({ ...f, [key]: value }));

  const parsed = form.amount.trim() ? parseAmount(form.amount) : null;
  const amount = parsed !== null && parsed !== '0.00' ? parsed : null;
  const change = priceChanges(price, form, amount);
  let error: string | null = null;
  if (form.name.trim() === '') error = 'Name is required';
  else if (form.amount.trim() && amount === null) error = 'Price must be a positive number';
  const ready = error === null && change !== null;
  const endError = endDay < tomorrow ? `The price can end ${tomorrow} at the earliest` : null;

  async function run(action: () => Promise<string>, charge = false) {
    setBusy(true);
    setProblem(null);
    setHint(false);
    try {
      onChanged(await action());
    } catch (failure) {
      setProblem(explain(failure));
      setHint(charge && failure instanceof ApiError && failure.status === 409);
      setEnding(false);
      setDeleting(false);
    } finally {
      setBusy(false);
    }
  }

  const save = () =>
    run(async () => {
      if (change === null) throw new Error('nothing changed');
      await patchPrice(price.id, change);
      return `Price ${form.name.trim()} saved`;
    }, change?.amount != null);
  const end = () =>
    run(async () => {
      await endPrice(price, endDay, context.timeZone);
      return `Price ${price.name} ends on ${endDay}`;
    });
  const remove = () =>
    run(async () => {
      await deletePrice(price.id);
      return `Price ${price.name} deleted`;
    });

  const message = problem ?? error ?? (ending ? endError : null);
  return (
    <Modal
      title={price.name}
      subtitle={price.priceType ?? undefined}
      width={520}
      onClose={onClose}
      ui="ui.catalog.priceDetail"
      footer={
        <>
          {message !== null && (
            <div className="footer-push bss-inline-error" role="alert">
              {message}
              {hint && ' Use Change price in the offering to replace the price of a live offering.'}
            </div>
          )}
          {deleting ? (
            <span className="bss-confirm">
              <span className="ask">Delete this price?</span>
              <Button variant="danger" small disabled={busy} onClick={() => void remove()}>
                Yes
              </Button>
              <Button small disabled={busy} onClick={() => setDeleting(false)}>
                No
              </Button>
            </span>
          ) : (
            <Button
              variant="quiet"
              tone="negative"
              data-ui="ui.catalog.deletePrice"
              onClick={() => setDeleting(true)}
            >
              Delete
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            data-ui="ui.catalog.editPrice"
            disabled={!ready || busy}
            onClick={() => void save()}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <dl className="bss-details">
          <dt>Charge</dt>
          <dd className="num">
            {price.cents === null ? '—' : formatCents(price.cents)} {price.currency ?? ''} /{' '}
            {price.interval}
          </dd>
          <dt>Validity</dt>
          <dd className="num">
            {price.validFrom === null
              ? 'any time'
              : businessDate(price.validFrom, context.timeZone)}{' '}
            –{' '}
            {price.validTo === null
              ? 'open-ended'
              : `ends ${businessDate(price.validTo, context.timeZone)}`}
          </dd>
        </dl>
        <label>
          Name
          <TextInput value={form.name} onChange={(e) => set('name')(e.target.value)} />
        </label>
        <label>
          Description
          <TextInput
            value={form.description}
            onChange={(e) => set('description')(e.target.value)}
          />
        </label>
        <label>
          Price, {price.currency ?? ''}
          <TextInput
            mono
            inputMode="decimal"
            value={form.amount}
            onChange={(e) => set('amount')(e.target.value)}
          />
          <span className="bss-secondary">
            The server refuses a charge change once the offering was ordered.
          </span>
        </label>
        {price.validTo === null &&
          (ending ? (
            <div className="bss-confirm">
              <TextInput
                mono
                type="date"
                aria-label="Price ends on"
                min={tomorrow}
                value={endDay}
                onChange={(e) => setEndDay(e.target.value)}
              />
              <Button
                variant="danger"
                small
                disabled={busy || endError !== null}
                onClick={() => void end()}
              >
                End on this date
              </Button>
              <Button small disabled={busy} onClick={() => setEnding(false)}>
                No
              </Button>
            </div>
          ) : (
            <div>
              <Button data-ui="ui.catalog.endPrice" onClick={() => setEnding(true)}>
                End price
              </Button>
            </div>
          ))}
      </div>
    </Modal>
  );
}
