import { useState } from 'react';
import {
  changePrice,
  createOffering,
  hasOpenPrice,
  loadCatalog,
  NEXT_LIFECYCLE,
  setLifecycle,
  shownPrice,
  type Catalog,
  type CatalogOffering,
  type Lifecycle,
} from '../api/bss';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { addDays, businessDate } from '../format/dates';
import { formatCents, formatInt, parseAmount } from '../format/money';
import { Button, Modal, PageTitle, Panel, Pill, Segmented, TextInput, useToast } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { checkOffering, type OfferingForm } from './newOffering';
import './screens.css';

// Screen 3. Offerings with their code (TMF620 externalIdentifier), specification, the
// recurring price in the UI currency, lifecycle status and validity. A row opens the
// offering's details, which carry the lifecycle step (In Design -> Active -> Retired):
// the design's table has no room for an action column at 1280px.

type Filter = 'All' | Lifecycle;
const FILTERS: Filter[] = ['All', 'In Design', 'Active', 'Retired'];

function columns(currency: string): Column[] {
  return [
    { key: 'code', label: 'Code', width: '150px', mono: true },
    { key: 'name', label: 'Name', width: '1fr' },
    { key: 'spec', label: 'Specification', width: '190px' },
    { key: 'price', label: `Price, ${currency}`, width: '140px', align: 'right', mono: true },
    { key: 'period', label: 'Period', width: '110px' },
    { key: 'status', label: 'Status', width: '120px' },
    { key: 'validity', label: 'Validity', width: '200px', mono: true },
  ];
}

/** The last valid day of an exclusive end instant, as a business date. */
function lastValidDay(endExclusive: string, timeZone: string): string {
  return businessDate(new Date(Date.parse(endExclusive) - 1).toISOString(), timeZone);
}

export function validity(offering: CatalogOffering, timeZone: string): string {
  const from =
    offering.validFrom === null ? 'any time' : businessDate(offering.validFrom, timeZone);
  const to = offering.validTo === null ? 'open-ended' : lastValidDay(offering.validTo, timeZone);
  return `${from} – ${to}`;
}

export function CatalogScreen() {
  useSetCrumbs([{ label: 'Product catalog' }], 'catalog');
  const context = useSystemContext();
  const toast = useToast();
  const [version, setVersion] = useState(0);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('All');
  const [creating, setCreating] = useState(false);
  const [fresh, setFresh] = useState<string | null>(null); // highlighted after creation
  const [open, setOpen] = useState<CatalogOffering | null>(null);
  const catalog = useResource(`catalog:${version}`, loadCatalog);
  const cols = columns(context.currency);

  const offerings = catalog.status === 'ok' ? catalog.data.offerings : [];
  const countOf = (f: Filter) =>
    f === 'All' ? offerings.length : offerings.filter((o) => o.status === f).length;
  const reset = () => {
    setQuery('');
    setFilter('All');
  };
  const q = query.trim().toLowerCase();
  const newButton = (
    <Button variant="primary" onClick={() => setCreating(true)} disabled={catalog.status !== 'ok'}>
      + New offering
    </Button>
  );

  return (
    <div className="bss-screen" data-ui="ui.catalog.list">
      <div className="bss-title-row">
        <PageTitle
          title="Product catalog"
          count={catalog.status === 'ok' ? formatInt(offerings.length) : undefined}
        />
        {newButton}
      </div>
      <div className="bss-toolbar">
        <TextInput
          width={300}
          placeholder="Search by code or name"
          aria-label="Search offerings"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Segmented
          label="Lifecycle status"
          value={filter}
          onChange={setFilter}
          options={FILTERS.map((key) => ({
            key,
            label: key,
            ...(catalog.status === 'ok' ? { count: countOf(key) } : {}),
          }))}
        />
      </div>
      <Panel>
        <TableHead columns={cols} />
        <ListState
          resource={catalog}
          columns={cols}
          errorTitle="Failed to load the catalog"
          empty={{
            title: 'Catalog is empty',
            text: 'Create the first product offering. It starts in In Design.',
            action: newButton,
          }}
          total={(data) => data.offerings.length}
          rows={(data) =>
            data.offerings.filter(
              (o) =>
                (filter === 'All' || o.status === filter) &&
                (!q ||
                  o.name.toLowerCase().includes(q) ||
                  (o.code ?? '').toLowerCase().includes(q)),
            )
          }
          onResetFilters={reset}
        >
          {(rows, data) => (
            <>
              {rows.map((offering) => {
                const price = shownPrice(offering.prices, context.now, context.currency);
                return (
                  <TableRow
                    key={offering.id}
                    columns={cols}
                    onClick={() => setOpen(offering)}
                    {...(offering.id === fresh ? { style: { background: 'var(--sel-3)' } } : {})}
                    cells={{
                      code: offering.code ?? <span className="bss-dash">—</span>,
                      name: (
                        <span
                          className={`bss-name ellipsis${offering.status === 'Retired' ? ' bss-retired' : ''}`}
                        >
                          {offering.name}
                        </span>
                      ),
                      spec: <span className="ellipsis">{offering.specification}</span>,
                      price:
                        price === null ? (
                          <span className="bss-dash">—</span>
                        ) : (
                          <div>
                            {formatCents(price.cents)}
                            {price.from !== null && (
                              <div className="bss-secondary">
                                from {businessDate(price.from, context.timeZone)}
                              </div>
                            )}
                          </div>
                        ),
                      period: price?.interval ?? 'month',
                      status: <Pill status={offering.status} />,
                      validity: validity(offering, context.timeZone),
                    }}
                  />
                );
              })}
              <TableFooter
                left={`Showing ${formatInt(rows.length)} of ${formatInt(data.offerings.length)}`}
                right="Prices exclude taxes · taxes are not applied"
              />
            </>
          )}
        </ListState>
      </Panel>

      {creating && catalog.status === 'ok' && (
        <NewOfferingModal
          catalog={catalog.data}
          onClose={() => setCreating(false)}
          onCreated={(id, code) => {
            setCreating(false);
            reset();
            setFresh(id);
            toast(`Offering ${code} created in In Design`);
            setVersion((v) => v + 1);
          }}
        />
      )}
      {open !== null && (
        <OfferingModal
          offering={open}
          onClose={() => setOpen(null)}
          onChanged={(message) => {
            setOpen(null);
            toast(message);
            setVersion((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}

// --- offering details and the lifecycle step ---

const CONFIRM: Record<Lifecycle, { button: string; ask: string }> = {
  Active: { button: 'Activate', ask: 'Activate? It becomes available to order.' },
  Retired: {
    button: 'Retire',
    ask: 'Retire? New orders are refused; existing products keep billing.',
  },
  'In Design': { button: '', ask: '' },
};

export function OfferingModal({
  offering,
  onClose,
  onChanged,
}: {
  offering: CatalogOffering;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const context = useSystemContext();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const next = NEXT_LIFECYCLE[offering.status];
  const label = offering.code ?? offering.name;

  async function step() {
    if (next === null) return;
    setBusy(true);
    setProblem(null);
    try {
      await setLifecycle(offering.id, next);
      onChanged(`Offering ${label} is now ${next}`);
    } catch (error) {
      setProblem(explain(error));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={offering.name}
      subtitle={[offering.code, offering.specification].filter(Boolean).join(' · ')}
      width={600}
      onClose={onClose}
      ui="ui.catalog.offeringDetail"
      footer={
        <>
          {problem !== null && (
            <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
              {problem}
            </div>
          )}
          {next === null ? (
            <span className="bss-hint" style={{ marginRight: 'auto' }}>
              Retired is final: the offering cannot be ordered again.
            </span>
          ) : confirming ? (
            <span className="bss-confirm">
              <span className="ask">{CONFIRM[next].ask}</span>
              <Button
                variant={next === 'Retired' ? 'danger' : 'primary'}
                small
                disabled={busy}
                onClick={() => void step()}
              >
                Yes
              </Button>
              <Button small disabled={busy} onClick={() => setConfirming(false)}>
                No
              </Button>
            </span>
          ) : (
            <Button
              variant={next === 'Retired' ? 'danger-outline' : 'primary'}
              data-ui="ui.catalog.lifecycle"
              onClick={() => setConfirming(true)}
            >
              {CONFIRM[next].button}
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <dl className="bss-details">
          <dt>Status</dt>
          <dd>
            <Pill status={offering.status} />
          </dd>
          <dt>Validity</dt>
          <dd className="num">{validity(offering, context.timeZone)}</dd>
          <dt>Prices</dt>
          <dd>
            {offering.prices.length === 0 ? (
              <span className="bss-dash">no recurring price</span>
            ) : (
              offering.prices.map((price) => (
                <div key={price.id} className="num">
                  {formatCents(price.cents)} {price.currency} / {price.interval ?? 'month'} ·{' '}
                  {businessDate(price.from, context.timeZone)} –{' '}
                  {price.to === null ? 'open-ended' : lastValidDay(price.to, context.timeZone)}
                </div>
              ))
            )}
          </dd>
        </dl>
        {hasOpenPrice(offering, context.currency) && (
          <PriceChangeSection offering={offering} label={label} onChanged={onChanged} />
        )}
      </div>
    </Modal>
  );
}

// --- change price (D-W21): one atomic internal operation ---

function PriceChangeSection({
  offering,
  label,
  onChanged,
}: {
  offering: CatalogOffering;
  label: string;
  onChanged: (message: string) => void;
}) {
  const context = useSystemContext();
  const tomorrow = addDays(context.businessDate, 1);
  const [open, setOpen] = useState(false);
  const [price, setPrice] = useState('');
  const [from, setFrom] = useState(tomorrow);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const parsed = price.trim() ? parseAmount(price) : null;
  const amount = parsed !== null && parsed !== '0.00' ? parsed : null;
  let error: string | null = null;
  if (price.trim() && amount === null) error = 'Price must be a positive number';
  else if (from && from < tomorrow)
    error = `The new price can start on ${tomorrow} at the earliest`;
  const ready = error === null && amount !== null && from !== '';

  async function submit() {
    if (!ready || amount === null) return;
    setBusy(true);
    setProblem(null);
    try {
      await changePrice(offering.id, amount, context.currency, from);
      onChanged(
        `Price of ${label} changes to ${formatCents(Number(amount.replace('.', '')))}` +
          ` ${context.currency} from ${from}`,
      );
    } catch (failure) {
      setProblem(explain(failure));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="bss-price-change">
        <Button data-ui="ui.catalog.changePrice" onClick={() => setOpen(true)}>
          Change price
        </Button>
      </div>
    );
  }
  const message = problem ?? error;
  return (
    <section className="bss-price-change" aria-label="Change price">
      <div className="bss-step-head">Change price</div>
      <div className="bss-form">
        <label>
          New price, {context.currency} /{' '}
          {offering.prices.find((p) => p.currency === context.currency)?.interval ?? 'month'}
          <TextInput
            mono
            inputMode="decimal"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
        <label>
          From
          <TextInput
            mono
            type="date"
            min={tomorrow}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
      </div>
      <p className="bss-hint bss-price-change-note">
        Existing products pick up the new price from their next billing period starting on or after{' '}
        {from || '…'}; new orders from {from || '…'}.
      </p>
      {message !== null && (
        <div className="bss-inline-error" role="alert">
          {message}
        </div>
      )}
      <div className="bss-confirm">
        <Button variant="primary" disabled={!ready || busy} onClick={() => void submit()}>
          Change price
        </Button>
        <Button onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </Button>
      </div>
    </section>
  );
}

// --- new offering ---

export function NewOfferingModal({
  catalog,
  onClose,
  onCreated,
}: {
  catalog: Catalog;
  onClose: () => void;
  onCreated: (id: string, code: string) => void;
}) {
  const context = useSystemContext();
  const tomorrow = addDays(context.businessDate, 1);
  const [form, setForm] = useState<OfferingForm>({
    code: '',
    name: '',
    specificationId: catalog.specifications[0]?.id ?? '',
    price: '',
    recurringPeriodType: 'monthly',
    recurringPeriodLength: '1',
    validFrom: tomorrow,
    validTo: '',
  });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const taken = new Set(catalog.offerings.flatMap((o) => (o.code ? [o.code] : [])));
  const checked = checkOffering(form, taken, context.businessDate);
  const set = (field: keyof OfferingForm) => (value: string) =>
    setForm((f) => ({ ...f, [field]: value }));

  async function create() {
    if (!checked.ready || checked.amount === null) return;
    setBusy(true);
    setProblem(null);
    try {
      const id = await createOffering(
        {
          code: checked.code,
          name: form.name.trim(),
          specificationId: form.specificationId,
          amount: checked.amount,
          recurringPeriodType: form.recurringPeriodType ?? 'monthly',
          recurringPeriodLength: Number(form.recurringPeriodLength ?? '1'),
          currency: context.currency,
          validFrom: form.validFrom,
          validTo: form.validTo || null,
        },
        context.timeZone,
      );
      onCreated(id, checked.code);
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  const message = problem ?? checked.error;
  return (
    <Modal
      title="New product offering"
      width={600}
      onClose={onClose}
      ui="ui.catalog.newOffering"
      footer={
        <>
          {message !== null && (
            <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
              {message}
            </div>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!checked.ready || busy} onClick={() => void create()}>
            Create in In Design
          </Button>
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <label>
          Code
          <TextInput
            mono
            value={form.code}
            style={{ textTransform: 'uppercase' }}
            onChange={(e) => set('code')(e.target.value)}
          />
        </label>
        <label>
          Name
          <TextInput value={form.name} onChange={(e) => set('name')(e.target.value)} />
        </label>
        <label>
          Specification
          <select
            className="bss-input"
            value={form.specificationId}
            onChange={(e) => set('specificationId')(e.target.value)}
          >
            {catalog.specifications.map((spec) => (
              <option key={spec.id} value={spec.id}>
                {spec.name}
              </option>
            ))}
          </select>
        </label>
        <div className="bss-form-pair">
          <label>
            Price, {context.currency}
            <TextInput
              mono
              inputMode="decimal"
              value={form.price}
              onChange={(e) => set('price')(e.target.value)}
            />
          </label>
          <label>
            Recurrence
            <select
              className="bss-input"
              value={form.recurringPeriodType}
              onChange={(e) => set('recurringPeriodType')(e.target.value)}
            >
              <option value="daily">day</option>
              <option value="weekly">week</option>
              <option value="monthly">month</option>
              <option value="yearly">year</option>
            </select>
          </label>
        </div>
        <label>
          Charge every
          <TextInput
            type="number"
            min="1"
            max="1000"
            step="1"
            value={form.recurringPeriodLength ?? '1'}
            onChange={(e) => set('recurringPeriodLength')(e.target.value)}
          />
          <span className="bss-secondary">
            Selected periods per charge; price covers the entire interval.
          </span>
        </label>
        <label>
          Valid from
          <TextInput
            mono
            type="date"
            min={tomorrow}
            value={form.validFrom}
            onChange={(e) => set('validFrom')(e.target.value)}
          />
        </label>
        <label>
          Valid to (empty = open-ended)
          <TextInput
            mono
            type="date"
            min={form.validFrom || tomorrow}
            value={form.validTo}
            onChange={(e) => set('validTo')(e.target.value)}
          />
        </label>
      </div>
    </Modal>
  );
}
