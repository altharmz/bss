import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { Fragment, useState } from 'react';
import {
  addPrice,
  changePrice,
  createOffering,
  deleteOffering,
  lastValidDate,
  loadCategories,
  loadOfferingDetail,
  hasOpenPrice,
  loadCatalog,
  NEXT_LIFECYCLE,
  setLifecycle,
  shownPrice,
  type Catalog,
  type CatalogOffering,
  type Lifecycle,
  type OfferingDetail,
  type Specification,
} from '../api/bss';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { addDays, businessDate } from '../format/dates';
import { formatCents, formatInt, parseAmount } from '../format/money';
import {
  SelectField,
  Button,
  Modal,
  Checkbox,
  PageHeader,
  Dialog,
  KeyValue,
  Menu,
  Tabs,
  TabPanel,
  StatusPill,
  Tag,
  Segmented,
  DateField,
  TextInput,
  useToast,
} from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { OfferingEditModal, PriceModal } from './OfferingEdit';
import { JobsTab } from './CatalogJobs';
import { CatalogsTab, CategoriesTab } from './Classification';
import { SpecificationModal, SpecificationsTab } from './Specifications';
import { checkOffering, type OfferingForm } from './newOffering';
import './screens.css';

// Screen 3. Offerings with their code (TMF620 externalIdentifier), specification, the
// recurring price in the UI currency, lifecycle status and validity. A row opens the
// offering's details, which carry the lifecycle step (In Design -> Active -> Retired):
// the design's table has no room for an action column at 1280px.

type Tab = 'offerings' | 'specifications' | 'categories' | 'catalogs' | 'jobs';
type Filter = 'All' | Lifecycle;
const FILTERS: Filter[] = ['All', 'In Design', 'Active', 'Retired'];
const GROUP_HINTS = {
  'In Design': 'Not sellable yet',
  Active: 'Sellable',
  Retired: 'Billing only',
};
function recurrence(interval: string): string {
  const singular: Record<string, string> = {
    day: 'Daily',
    week: 'Weekly',
    month: 'Monthly',
    year: 'Yearly',
  };
  return singular[interval] ?? `Every ${interval}`;
}

function columns(currency: string): Column[] {
  return [
    { key: 'name', label: 'Offering', width: 'minmax(240px, 1fr)' },
    { key: 'status', label: 'Lifecycle', width: '140px' },
    { key: 'period', label: 'Recurrence', width: '150px' },
    { key: 'price', label: `Price ${currency}`, width: '170px', align: 'right', mono: true },
    { key: 'updated', label: 'Updated', width: '130px', mono: true, align: 'right' },
  ];
}

const lastValidDay = lastValidDate;

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
  const [tab, setTab] = useState<Tab>('offerings');
  const [creating, setCreating] = useState(false);
  const [creatingSpec, setCreatingSpec] = useState(false);
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
  const [newRequests, setNewRequests] = useState(0);
  const newButton = (
    <Button variant="primary" onClick={() => setCreating(true)} disabled={catalog.status !== 'ok'}>
      + New offering
    </Button>
  );
  const specs = tab === 'specifications';
  const offeringsTab = tab === 'offerings';

  return (
    <div className="bss-screen" data-ui="ui.catalog.list">
      <PageHeader
        title="Product catalog"
        count={
          catalog.status === 'ok' && (offeringsTab || specs)
            ? `${formatInt(specs ? catalog.data.specifications.length : offerings.length)} ${specs ? 'specifications' : 'offerings'}`
            : undefined
        }
        actions={
          tab === 'categories' ? (
            <Button
              variant="primary"
              data-ui="ui.catalog.newCategory"
              disabled={catalog.status !== 'ok'}
              onClick={() => setNewRequests((n) => n + 1)}
            >
              + New category
            </Button>
          ) : tab === 'catalogs' ? (
            <Button
              variant="primary"
              data-ui="ui.catalog.newCatalog"
              onClick={() => setNewRequests((n) => n + 1)}
            >
              + New catalog
            </Button>
          ) : specs ? (
            <Button
              variant="primary"
              data-ui="ui.catalog.newSpecification"
              onClick={() => setCreatingSpec(true)}
              disabled={catalog.status !== 'ok'}
            >
              + New specification
            </Button>
          ) : (
            offeringsTab && newButton
          )
        }
      />
      <div className="bss-toolbar">
        <Tabs
          id="catalog"
          label="Catalog sections"
          value={tab}
          onChange={setTab}
          options={[
            { key: 'offerings', label: 'Offerings' },
            { key: 'specifications', label: 'Specifications' },
            { key: 'categories', label: 'Categories' },
            { key: 'catalogs', label: 'Catalogs' },
            { key: 'jobs', label: 'Import / export' },
          ]}
        />
      </div>
      <TabPanel id="catalog" value={tab}>
        {tab !== 'jobs' && (
          <div className="bss-toolbar">
            <TextInput
              width={300}
              placeholder={
                specs
                  ? 'Search by name or brand'
                  : offeringsTab
                    ? 'Search by code or name'
                    : 'Search by name'
              }
              aria-label={
                specs
                  ? 'Search specifications'
                  : offeringsTab
                    ? 'Search offerings'
                    : `Search ${tab}`
              }
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {offeringsTab && (
              <Segmented
                label="Lifecycle status"
                value={filter}
                onChange={setFilter}
                options={FILTERS.map((key) => ({
                  key,
                  label: key === 'In Design' ? 'In design' : key,
                  ...(catalog.status === 'ok' ? { count: countOf(key) } : {}),
                }))}
              />
            )}
          </div>
        )}
        {specs && (
          <div data-ui="ui.catalog.specifications">
            <SpecificationsTab
              resource={catalog}
              query={query}
              onChanged={(message) => {
                toast(message);
                setVersion((v) => v + 1);
              }}
            />
          </div>
        )}
        {tab === 'categories' && (
          <CategoriesTab
            offerings={catalog.status === 'ok' ? catalog.data : null}
            query={query}
            newRequests={newRequests}
          />
        )}
        {tab === 'catalogs' && <CatalogsTab query={query} newRequests={newRequests} />}
        {tab === 'jobs' && <JobsTab />}
        {offeringsTab && (
          <div className="catalog-offerings" role="table" aria-label="Offerings">
            <TableHead columns={cols} />
            <ListState
              resource={catalog}
              columns={cols}
              errorTitle="Failed to load the catalog"
              empty={{
                title: 'Catalog is empty',
                text: 'Create the first product offering. It starts in In design.',
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
                  {(['In Design', 'Active', 'Retired'] as const).map((status) => {
                    const group = rows.filter((offering) => offering.status === status);
                    if (group.length === 0) return null;
                    return (
                      <Fragment key={status}>
                        <div
                          role="row"
                          className="catalog-group"
                          data-ui="ui.catalog.lifecycleGroup"
                        >
                          <span role="rowheader">
                            {status === 'In Design' ? 'In design' : status}
                          </span>
                          <span className="mono">{formatInt(group.length)}</span>
                          <span>{GROUP_HINTS[status]}</span>
                        </div>
                        {group.map((offering) => {
                          const price = shownPrice(offering.prices, context.now, context.currency);
                          return (
                            <TableRow
                              key={offering.id}
                              columns={cols}
                              onClick={() => setOpen(offering)}
                              className={offering.id === fresh ? 'catalog-fresh' : ''}
                              cells={{
                                name: (
                                  <div className="catalog-offering-name">
                                    <span
                                      className={`bss-name ellipsis${offering.status === 'Retired' ? ' bss-retired' : ''}`}
                                    >
                                      {offering.name}
                                    </span>
                                    <span className="catalog-code">{offering.code ?? '—'}</span>
                                    {!offering.isSellable && (
                                      <span data-ui="ui.catalog.notSellable">
                                        <Tag>Not sellable</Tag>
                                      </span>
                                    )}
                                    <span className="sr-only">{offering.specification}</span>
                                    <span className="sr-only">
                                      {validity(offering, context.timeZone)}
                                    </span>
                                  </div>
                                ),
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
                                period: recurrence(price?.interval ?? 'month'),
                                status: <StatusPill kind="lifecycle" status={offering.status} />,
                                updated: <span className="bss-dash">—</span>,
                              }}
                            />
                          );
                        })}
                      </Fragment>
                    );
                  })}
                  <TableFooter
                    left={`Showing ${formatInt(rows.length)} of ${formatInt(data.offerings.length)}`}
                    right="Prices exclude taxes · taxes are not applied"
                  />
                </>
              )}
            </ListState>
          </div>
        )}
        {offeringsTab && (
          <Note tone="info" className="catalog-lifecycle-note" data-ui="ui.catalog.lifecycleNote">
            Lifecycle runs one way: In design → Active → Retired. Retired offerings stay billable
            for products already on them and are hidden from Add product.
          </Note>
        )}
      </TabPanel>

      {creatingSpec && (
        <SpecificationModal
          onClose={() => setCreatingSpec(false)}
          onCreated={(created) => {
            setCreatingSpec(false);
            toast(`Specification ${created.name} created`);
            setVersion((v) => v + 1);
          }}
        />
      )}
      {creating && catalog.status === 'ok' && (
        <NewOfferingModal
          catalog={catalog.data}
          onClose={() => setCreating(false)}
          onCreated={(id, code) => {
            setCreating(false);
            reset();
            setFresh(id);
            toast(`Offering ${code} created in In design`);
            setVersion((v) => v + 1);
          }}
        />
      )}
      {open !== null && (
        <OfferingModal
          offering={open}
          specifications={catalog.status === 'ok' ? catalog.data.specifications : []}
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
const PRICE_COLUMNS: Column[] = [
  { key: 'from', label: 'From', width: '1fr', mono: true },
  { key: 'to', label: 'To', width: '1fr', mono: true },
  { key: 'amount', label: 'Amount', width: '2fr', mono: true, align: 'right' },
];

const CONFIRM: Record<Lifecycle, { button: string; ask: string }> = {
  Active: { button: 'Activate…', ask: 'Activate? It becomes available to order.' },
  Retired: {
    button: 'Retire…',
    ask: 'Retire? New orders are refused; existing products keep billing.',
  },
  'In Design': { button: '', ask: '' },
};

export function OfferingModal({
  offering,
  specifications = [],
  onClose,
  onChanged,
}: {
  offering: CatalogOffering;
  specifications?: Specification[];
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const context = useSystemContext();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [price, setPrice] = useState<string | null>(null);
  const [addingPrice, setAddingPrice] = useState(false);
  const detail = useResource(`offering:${offering.id}`, (signal) =>
    loadOfferingDetail(offering.id, signal),
  );
  const next = NEXT_LIFECYCLE[offering.status];
  // The server refuses a delete of an ordered offering or one with prices; the UI hides
  // it only where the loaded data already makes that certain.
  const deletable =
    detail.status === 'ok' &&
    detail.data.status !== 'Active' &&
    detail.data.status !== 'Retired' &&
    detail.data.priceRefs.length === 0;

  async function remove() {
    setBusy(true);
    setProblem(null);
    try {
      await deleteOffering(offering.id);
      onChanged(`Offering ${offering.code ?? offering.name} deleted`);
    } catch (error) {
      setProblem(explain(error));
      setDeleting(false);
    } finally {
      setBusy(false);
    }
  }
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
    <>
      <Dialog
        onSubmit={submitDialogPrimary}
        title={offering.name}
        context={[offering.code, offering.specification].filter(Boolean).join(' · ')}
        size="L"
        headerActions={
          <Menu
            label="Offering actions"
            items={[
              ...(detail.status === 'ok'
                ? [
                    {
                      label: 'Edit offering',
                      ui: 'ui.catalog.editOffering',
                      onClick: () => setEditing(true),
                    },
                  ]
                : []),
              ...(deletable
                ? [
                    {
                      label: 'Delete',
                      tone: 'negative' as const,
                      ui: 'ui.catalog.deleteOffering',
                      onClick: () => setDeleting(true),
                    },
                  ]
                : []),
            ]}
          />
        }
        onClose={price !== null || editing || addingPrice ? () => undefined : onClose}
        ui="ui.catalog.offeringDetail"
        footer={
          <>
            {problem !== null && (
              <Note tone="negative" className="footer-push">
                {problem}
              </Note>
            )}
            {deletable && deleting && (
              <span className="bss-confirm">
                <span className="ask">Delete this offering?</span>
                <Button variant="danger" small disabled={busy} onClick={() => void remove()}>
                  Yes
                </Button>
                <Button small disabled={busy} onClick={() => setDeleting(false)}>
                  No
                </Button>
              </span>
            )}
            {next === null ? (
              <Note tone="info" className="footer-push">
                Retired is final: the offering cannot be ordered again.
              </Note>
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
                variant={next === 'Retired' ? 'quiet' : 'primary'}
                {...(next === 'Retired' ? { tone: 'negative' as const } : {})}
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
        <div className="catalog-detail">
          <ol
            className="catalog-stepper"
            aria-label="Lifecycle"
            data-ui="ui.catalog.lifecycleStepper"
          >
            {(['In Design', 'Active', 'Retired'] as const).map((status, index) => {
              const current = ['In Design', 'Active', 'Retired'].indexOf(offering.status);
              return (
                <li
                  key={status}
                  className={index < current ? 'done' : index === current ? 'current' : 'later'}
                  aria-current={index === current ? 'step' : undefined}
                >
                  <span className="catalog-step-track" aria-hidden="true">
                    <span />
                  </span>
                  <strong>{status === 'In Design' ? 'In design' : status}</strong>
                  <span className="catalog-step-caption">{GROUP_HINTS[status]}</span>
                </li>
              );
            })}
          </ol>
          <div className="catalog-price-figure" data-ui="ui.catalog.priceFigure">
            {(() => {
              const p = shownPrice(offering.prices, context.now, context.currency);
              return p ? (
                <>
                  {formatCents(p.cents)}
                  <span>
                    {context.currency} / {p.interval ?? 'month'}
                  </span>
                </>
              ) : (
                '—'
              );
            })()}
          </div>
          <KeyValue
            items={[
              [
                'Recurrence',
                recurrence(
                  shownPrice(offering.prices, context.now, context.currency)?.interval ?? 'month',
                ),
              ],
              ['Code', detail.status === 'ok' ? (detail.data.code ?? '—') : (offering.code ?? '—')],
              [
                'Specification',
                detail.status === 'ok'
                  ? (detail.data.specification?.name ?? '—')
                  : offering.specification,
              ],
              [
                'Valid from',
                offering.validFrom === null
                  ? 'any time'
                  : businessDate(offering.validFrom, context.timeZone),
              ],
            ]}
          />
          <dl className="bss-details">
            <dt>Status</dt>
            <dd>
              <StatusPill kind="lifecycle" status={offering.status} />
            </dd>
            <dt>Validity</dt>
            <dd className="num">{validity(offering, context.timeZone)}</dd>
            {detail.status === 'ok' && (
              <>
                <dt>Description</dt>
                <dd>{detail.data.description ?? <span className="bss-dash">—</span>}</dd>
                <dt>Sellable</dt>
                <dd>{detail.data.isSellable ? 'yes' : 'no'}</dd>
                <dt>Categories</dt>
                <dd>
                  {detail.data.categories.length === 0 ? (
                    <span className="bss-dash">none</span>
                  ) : (
                    detail.data.categories.map((c) => c.name).join(', ')
                  )}
                </dd>
              </>
            )}
            {detail.status === 'error' && (
              <>
                <dt>Details</dt>
                <dd>
                  <Note tone="negative">{detail.error.message}</Note>
                </dd>
              </>
            )}
            <dt>Prices</dt>
            <dd>
              {offering.prices.length === 0 ? (
                <span className="bss-dash">no recurring price</span>
              ) : (
                <div role="table" aria-label="Prices">
                  <TableHead density="compact" columns={PRICE_COLUMNS} />
                  {offering.prices.map((p) => (
                    <TableRow
                      key={p.id}
                      density="compact"
                      columns={PRICE_COLUMNS}
                      cells={{
                        from: businessDate(p.from, context.timeZone),
                        to: p.to === null ? 'open-ended' : lastValidDay(p.to, context.timeZone),
                        amount: (
                          <>
                            <button
                              type="button"
                              className="bss-link"
                              aria-label={`Open price ${formatCents(p.cents)}`}
                              onClick={() => setPrice(p.id)}
                            >
                              <span aria-hidden="true">
                                {formatCents(p.cents)} {p.currency} / {p.interval ?? 'month'}
                              </span>
                              <span className="sr-only">
                                {formatCents(p.cents)} {p.currency} / {p.interval ?? 'month'} ·{' '}
                                {businessDate(p.from, context.timeZone)} –{' '}
                                {p.to === null
                                  ? 'open-ended'
                                  : lastValidDay(p.to, context.timeZone)}
                              </span>
                            </button>
                          </>
                        ),
                      }}
                    />
                  ))}
                </div>
              )}
            </dd>
          </dl>
          {detail.status === 'ok' && (
            <Button small onClick={() => setAddingPrice(true)}>
              Add price…
            </Button>
          )}
          {hasOpenPrice(offering, context.currency) && (
            <PriceChangeSection offering={offering} label={label} onChanged={onChanged} />
          )}
        </div>
      </Dialog>
      {editing && detail.status === 'ok' && (
        <OfferingEditModal
          detail={detail.data}
          specifications={specifications}
          onClose={() => setEditing(false)}
          onSaved={onChanged}
        />
      )}
      {addingPrice && detail.status === 'ok' && (
        <AddPriceModal
          detail={detail.data}
          onClose={() => setAddingPrice(false)}
          onChanged={onChanged}
        />
      )}
      {price !== null && (
        <PriceModal priceId={price} onClose={() => setPrice(null)} onChanged={onChanged} />
      )}
    </>
  );
}

function AddPriceModal({
  detail,
  onClose,
  onChanged,
}: {
  detail: OfferingDetail;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const context = useSystemContext();
  const today = context.businessDate;
  const [priceType, setPriceType] = useState<'oneTime' | 'recurring'>('oneTime');
  const [amount, setAmount] = useState('');
  const [validFrom, setValidFrom] = useState(today);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const parsed = amount.trim() ? parseAmount(amount) : null;
  const canonical = parsed !== null && parsed !== '0.00' ? parsed : null;
  let error: string | null = null;
  if (amount.trim() && canonical === null) error = 'Price must be a positive number';
  else if (validFrom && validFrom < today) error = `Valid from cannot be earlier than ${today}`;
  const ready = error === null && canonical !== null && validFrom !== '';

  async function submit() {
    if (!ready || canonical === null) return;
    setBusy(true);
    setProblem(null);
    try {
      await addPrice(
        {
          offeringId: detail.id,
          offeringName: detail.name,
          priceType,
          amount: canonical,
          currency: context.currency,
          validFrom,
          existingPriceIds: detail.priceRefs.map((price) => price.id),
        },
        context.timeZone,
      );
      onChanged(`Price added to ${detail.name}`);
    } catch (failure) {
      setProblem(explain(failure));
    } finally {
      setBusy(false);
    }
  }

  const message = problem ?? error;
  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title={`Add price · ${detail.name}`}
      width={520}
      onClose={busy ? () => undefined : onClose}
      ui="ui.catalog.addPrice"
      footer={
        <>
          {message !== null && (
            <Note tone="negative" className="footer-push">
              {message}
            </Note>
          )}
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!ready || busy} onClick={() => void submit()}>
            Add price
          </Button>
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <label htmlFor="add-price-type">Charge type</label>
        <SelectField
          id="add-price-type"
          value={priceType}
          onChange={(event) => setPriceType(event.target.value as 'oneTime' | 'recurring')}
        >
          <option value="oneTime">One time</option>
          <option value="recurring">Monthly recurring</option>
        </SelectField>
        <label htmlFor="add-price-amount">Amount, {context.currency}</label>
        <TextInput
          id="add-price-amount"
          mono
          inputMode="decimal"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
        />
        <label htmlFor="add-price-from">Valid from</label>
        <DateField
          id="add-price-from"
          min={today}
          value={validFrom}
          onChange={(event) => setValidFrom(event.target.value)}
        />
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
          Change price…
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
          <DateField min={tomorrow} value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
      </div>
      <Note tone="info" className="bss-price-change-note">
        Existing products pick up the new price from their next billing period starting on or after{' '}
        {from || '…'}; new orders from {from || '…'}.
      </Note>
      {message !== null && <Note tone="negative">{message}</Note>}
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
  const categories = useResource('categories', loadCategories);
  const [form, setForm] = useState<OfferingForm>({
    code: '',
    name: '',
    description: '',
    isSellable: true,
    categoryIds: [],
    specificationId: catalog.specifications[0]?.id ?? '',
    price: '',
    recurringPeriodType: 'monthly',
    recurringPeriodLength: '1',
    validFrom: tomorrow,
    validTo: '',
  });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [extra, setExtra] = useState<Specification[]>([]); // created from this dialog
  const [creatingSpec, setCreatingSpec] = useState(false);
  const specifications = [...catalog.specifications, ...extra];
  const taken = new Set(catalog.offerings.flatMap((o) => (o.code ? [o.code] : [])));
  const checked = checkOffering(form, taken, context.businessDate);
  function set<K extends keyof OfferingForm>(field: K) {
    return (value: OfferingForm[K]) => setForm((f) => ({ ...f, [field]: value }));
  }

  async function create() {
    if (!checked.ready || checked.amount === null || categories.status !== 'ok') return;
    setBusy(true);
    setProblem(null);
    try {
      const id = await createOffering(
        {
          code: checked.code,
          name: form.name.trim(),
          description: form.description,
          isSellable: form.isSellable,
          categoryIds: form.categoryIds,
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
    <>
      <Modal
        onSubmit={submitDialogPrimary}
        title="New product offering"
        width={600}
        onClose={creatingSpec ? () => undefined : onClose}
        ui="ui.catalog.newOffering"
        footer={
          <>
            {message !== null && (
              <Note tone="negative" className="footer-push">
                {message}
              </Note>
            )}
            <Button onClick={onClose}>Cancel</Button>
            <Button
              variant="primary"
              disabled={!checked.ready || categories.status !== 'ok' || busy}
              onClick={() => void create()}
            >
              Create as In design
            </Button>
          </>
        }
      >
        <div className="bss-modal-body bss-form">
          <label htmlFor="new-offering-code">Code</label>
          <TextInput
            id="new-offering-code"
            className="uppercase"
            mono
            value={form.code}
            onChange={(e) => set('code')(e.target.value)}
          />
          <label htmlFor="new-offering-name">Name</label>
          <TextInput
            id="new-offering-name"
            value={form.name}
            onChange={(e) => set('name')(e.target.value)}
          />
          <label htmlFor="new-offering-description">Description</label>
          <TextInput
            id="new-offering-description"
            value={form.description}
            onChange={(e) => set('description')(e.target.value)}
          />
          <Checkbox
            checked={form.isSellable}
            onChange={(e) => set('isSellable')(e.target.checked)}
            label="Sellable"
          />
          <label htmlFor="new-offering-specification">Specification</label>
          <SelectField
            id="new-offering-specification"
            value={form.specificationId}
            onChange={(e) => set('specificationId')(e.target.value)}
          >
            {specifications.map((spec) => (
              <option key={spec.id} value={spec.id}>
                {spec.name}
              </option>
            ))}
          </SelectField>
          <div>
            <Button small onClick={() => setCreatingSpec(true)}>
              New specification…
            </Button>
          </div>
          <label htmlFor="new-offering-categories">Categories</label>
          <SelectField
            id="new-offering-categories"
            multiple
            disabled={categories.status !== 'ok'}
            value={form.categoryIds}
            onChange={(e) =>
              set('categoryIds')(Array.from(e.target.selectedOptions, (option) => option.value))
            }
          >
            {categories.status === 'ok' &&
              categories.data.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
          </SelectField>
          {categories.status === 'loading' && <Note tone="info">Loading categories…</Note>}
          {categories.status === 'error' && <Note tone="negative">{categories.error.message}</Note>}
          <div className="bss-form-pair">
            <div>
              <label htmlFor="new-offering-price">Price, {context.currency}</label>
              <TextInput
                id="new-offering-price"
                mono
                inputMode="decimal"
                value={form.price}
                onChange={(e) => set('price')(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="new-offering-recurrence">Recurrence</label>
              <SelectField
                id="new-offering-recurrence"
                value={form.recurringPeriodType}
                onChange={(e) => set('recurringPeriodType')(e.target.value)}
              >
                <option value="daily">day</option>
                <option value="weekly">week</option>
                <option value="monthly">month</option>
                <option value="yearly">year</option>
              </SelectField>
            </div>
          </div>
          <label htmlFor="new-offering-charge-every">Charge every</label>
          <TextInput
            id="new-offering-charge-every"
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
          <label htmlFor="new-offering-valid-from">Valid from</label>
          <DateField
            id="new-offering-valid-from"
            min={tomorrow}
            value={form.validFrom}
            onChange={(e) => set('validFrom')(e.target.value)}
          />
          <label htmlFor="new-offering-valid-to">Valid to (empty = open-ended)</label>
          <DateField
            id="new-offering-valid-to"
            min={form.validFrom || tomorrow}
            value={form.validTo}
            onChange={(e) => set('validTo')(e.target.value)}
          />
        </div>
      </Modal>
      {creatingSpec && (
        <SpecificationModal
          onClose={() => setCreatingSpec(false)}
          onCreated={(created) => {
            setExtra((list) => [...list, created]);
            set('specificationId')(created.id);
            setCreatingSpec(false);
          }}
        />
      )}
    </>
  );
}
