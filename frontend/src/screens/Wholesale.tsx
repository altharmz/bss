import { useRef, useState, type ReactNode } from 'react';
import { explain, type Page } from '../api/client';
import { loadProduct } from '../api/products';
import { useResource } from '../api/useResource';
import {
  importInvoice,
  loadDiscrepancies,
  loadInvoice,
  loadInvoices,
  loadWholesaleLines,
  WHOLESALE_PAGE,
  type DiscrepancyKind,
  type Invoice,
} from '../api/wholesale';
import { useSetCrumbs } from '../app/context';
import { hrefOf, Link, useRouter } from '../app/router';
import { lastDay } from '../format/dates';
import { formatInt, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { Note } from '../ui/Note';
import { TableHead, TableRow, type Column } from '../ui/Table';
import {
  Button,
  Dialog,
  Field,
  Panel,
  SelectField,
  TabPanel,
  Tabs,
  TextInput,
  submitDialogPrimary,
} from '../ui/kit';
import { PageFooter } from '../ui/paging';
import { ErrorBlock, ListState } from '../ui/states';
import { MoneyStrip, PageHeader } from '../ui/summaries';

const KINDS: Record<DiscrepancyKind, string> = {
  billedNotActive: 'Billed while inactive',
  activeNotBilled: 'Active without a charge',
  usageMismatch: 'Usage mismatch',
  duplicateCharge: 'Duplicate charge',
};

const INVOICE_COLUMNS: Column[] = [
  { key: 'number', label: 'Invoice number', width: 'minmax(150px, 1fr)' },
  { key: 'period', label: 'Period', width: 'minmax(185px, 1fr)', mono: true },
  { key: 'total', label: 'Total', width: 'minmax(120px, 1fr)', align: 'right' },
  { key: 'discrepancies', label: 'Discrepancies', width: '115px', align: 'right', mono: true },
  { key: 'margin', label: 'Margin', width: 'minmax(120px, 1fr)', align: 'right' },
];

const DISCREPANCY_COLUMNS: Column[] = [
  { key: 'kind', label: 'Kind', width: 'minmax(150px, 1fr)' },
  { key: 'subscriber', label: 'Subscriber', width: 'minmax(145px, 1fr)' },
  { key: 'number', label: 'Number', width: 'minmax(125px, 1fr)', mono: true },
  { key: 'row', label: 'CSV row', width: '76px', align: 'right', mono: true },
  { key: 'expected', label: 'Expected', width: 'minmax(95px, 1fr)', align: 'right' },
  { key: 'invoiced', label: 'Invoiced', width: 'minmax(95px, 1fr)', align: 'right' },
];

const LINE_COLUMNS: Column[] = [
  { key: 'number', label: 'Number', width: 'minmax(125px, 1fr)', mono: true },
  { key: 'iccid', label: 'ICCID', width: 'minmax(190px, 1fr)', mono: true },
  { key: 'rows', label: 'CSV rows', width: '88px', align: 'right', mono: true },
  { key: 'cost', label: 'Cost', width: 'minmax(120px, 1fr)', align: 'right' },
  { key: 'revenue', label: 'Revenue', width: 'minmax(120px, 1fr)', align: 'right' },
  { key: 'margin', label: 'Margin', width: 'minmax(120px, 1fr)', align: 'right' },
];

const INVOICE_NUMBER = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$/;
const WHOLESALE = hrefOf({ name: 'wholesale' });

function invoiceHref(id: string) {
  return hrefOf({ name: 'wholesale', invoiceId: id });
}

function money(amount: Invoice['total']) {
  return <Amount cents={toCents(amount.value)} currency={amount.unit} />;
}

function period({ start, end }: Invoice['period']) {
  return `${start} – ${lastDay(end)}`;
}

function discrepancyCount(invoice: Invoice) {
  return Object.values(invoice.discrepancies).reduce((total, count) => total + count, 0);
}

export function WholesaleScreen() {
  useSetCrumbs([{ label: 'Wholesale' }], 'wholesale');
  const { navigate } = useRouter();
  const [offset, setOffset] = useState(0);
  const [importing, setImporting] = useState(false);
  return (
    <div data-ui="ui.wholesale">
      <PageHeader
        title="Wholesale"
        actions={
          <Button variant="primary" onClick={() => setImporting(true)}>
            Import invoice
          </Button>
        }
      />
      <WholesaleTable
        title="Invoices"
        columns={INVOICE_COLUMNS}
        offset={offset}
        onOffset={setOffset}
        load={loadInvoices}
        resourceKey="wholesale invoices"
        empty={{ title: 'No invoices', text: 'Import a partner invoice to reconcile it.' }}
      >
        {(invoice) => (
          <TableRow
            key={invoice.id}
            columns={INVOICE_COLUMNS}
            cells={{
              number: (
                <Link className="bss-link" href={invoiceHref(invoice.id)}>
                  {invoice.invoiceNumber}
                </Link>
              ),
              period: period(invoice.period),
              total: money(invoice.total),
              discrepancies: formatInt(discrepancyCount(invoice)),
              margin: money(invoice.margin),
            }}
          />
        )}
      </WholesaleTable>
      {importing && (
        <ImportDialog
          onClose={() => setImporting(false)}
          onImported={(invoice) => navigate(invoiceHref(invoice.id))}
        />
      )}
    </div>
  );
}

function ImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (invoice: Invoice) => void;
}) {
  const [number, setNumber] = useState('');
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const submitting = useRef(false);
  const validNumber = INVOICE_NUMBER.test(number.trim());

  async function submit() {
    if (submitting.current || !file || !validNumber) return;
    submitting.current = true;
    setBusy(true);
    setProblem('');
    try {
      onImported(await importInvoice(number.trim(), file));
    } catch (error) {
      setProblem(explain(error));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      title="Import invoice"
      size="M"
      ui="ui.wholesale.import"
      onClose={() => {
        if (!submitting.current) onClose();
      }}
      onSubmit={submitDialogPrimary}
      footer={
        <>
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            data-dialog-primary
            disabled={busy || !validNumber || !file}
            onClick={() => void submit()}
          >
            {busy ? 'Importing…' : 'Import'}
          </Button>
        </>
      }
    >
      <Field
        label="Invoice number"
        help="Start with a letter or digit. Up to 64 characters using letters, digits, or . _ / -"
      >
        <TextInput
          value={number}
          disabled={busy}
          onChange={(event) => setNumber(event.target.value)}
        />
      </Field>
      <Field label="Invoice CSV">
        <input
          type="file"
          accept=".csv,text/csv"
          disabled={busy}
          onChange={(event) => setFile(event.target.files?.[0])}
        />
      </Field>
      {problem && <Note tone="negative">{problem}</Note>}
    </Dialog>
  );
}

const TABS = [
  { key: 'discrepancies', label: 'Discrepancies' },
  { key: 'lines', label: 'Lines' },
] as const;
const TAB_ID = 'wholesale-invoice';

export function WholesaleInvoiceScreen({ id }: { id: string }) {
  const resource = useResource(id, (signal) => loadInvoice(id, signal));
  const [tab, setTab] = useState<(typeof TABS)[number]['key']>('discrepancies');
  const invoice = resource.status === 'ok' ? resource.data : null;
  const title = invoice?.invoiceNumber ?? 'Wholesale invoice';
  useSetCrumbs([{ label: 'Wholesale', href: WHOLESALE }, { label: title }], `${id}:${title}`);
  return (
    <div data-ui="ui.wholesale.detail">
      <PageHeader title={title} meta={invoice && period(invoice.period)} />
      {resource.status === 'error' && (
        <ErrorBlock
          title="Failed to load invoice"
          error={resource.error}
          onRetry={resource.retry}
        />
      )}
      {resource.status === 'loading' && <p role="status">Loading invoice…</p>}
      {invoice && (
        <>
          <MoneyStrip
            figures={[
              { label: 'Cost', value: money(invoice.cost) },
              { label: 'Revenue', value: money(invoice.revenue) },
              { label: 'Margin', value: money(invoice.margin), emphasis: 'answer' },
              { label: 'Line count', value: formatInt(invoice.lineCount) },
            ]}
          />
          <Tabs
            id={TAB_ID}
            label="Invoice reconciliation"
            options={[...TABS]}
            value={tab}
            onChange={setTab}
          />
          <TabPanel id={TAB_ID} value={tab}>
            {tab === 'discrepancies' ? <Discrepancies invoice={invoice} /> : <Lines id={id} />}
          </TabPanel>
        </>
      )}
    </div>
  );
}

function Discrepancies({ invoice }: { invoice: Invoice }) {
  const [kind, setKind] = useState<DiscrepancyKind | ''>('');
  const [offset, setOffset] = useState(0);
  function filter(next: DiscrepancyKind | '') {
    setKind(next);
    setOffset(0);
  }
  return (
    <section data-ui="ui.wholesale.discrepancies">
      <WholesaleTable
        title="Discrepancies"
        columns={DISCREPANCY_COLUMNS}
        offset={offset}
        onOffset={setOffset}
        load={(offset, signal) => loadDiscrepancies(invoice.id, kind, offset, signal)}
        resourceKey={`discrepancies:${invoice.id}:${kind}`}
        empty={
          kind
            ? {
                title: 'No matching discrepancies',
                text: KINDS[kind],
                action: <Button onClick={() => filter('')}>Reset filters</Button>,
              }
            : { title: 'No discrepancies', text: 'This invoice reconciled without discrepancies.' }
        }
        controls={
          <SelectField
            aria-label="Discrepancy kind"
            value={kind}
            onChange={(event) => filter(event.target.value as DiscrepancyKind | '')}
          >
            <option value="">All kinds</option>
            {Object.entries(KINDS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </SelectField>
        }
      >
        {(row) => (
          <TableRow
            key={row.id}
            columns={DISCREPANCY_COLUMNS}
            cells={{
              kind: (
                <>
                  {KINDS[row.kind]}
                  {row.usageKind && ` · ${row.usageKind}`}
                </>
              ),
              subscriber: row.product ? <SubscriberLink id={row.product.id} /> : '—',
              number: row.msisdn ?? '—',
              row: row.lineNumber ?? '—',
              expected: row.expected ?? '—',
              invoiced: row.invoiced ?? '—',
            }}
          />
        )}
      </WholesaleTable>
    </section>
  );
}

function SubscriberLink({ id }: { id: string }) {
  const product = useResource(id, (signal) => loadProduct(id, signal));
  if (product.status === 'loading') return <span aria-label="Loading subscriber">…</span>;
  if (product.status === 'error')
    return (
      <Button small onClick={product.retry}>
        Retry subscriber
      </Button>
    );
  const account = product.data.billingAccount;
  return account?.id ? (
    <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId: account.id })}>
      {account.name || 'Subscriber card'}
    </Link>
  ) : (
    '—'
  );
}

function Lines({ id }: { id: string }) {
  const [sort, setSort] = useState<'margin' | '-margin'>('margin');
  const [offset, setOffset] = useState(0);
  return (
    <section data-ui="ui.wholesale.lines">
      <WholesaleTable
        title="Lines"
        columns={LINE_COLUMNS}
        offset={offset}
        onOffset={setOffset}
        resourceKey={`lines:${id}:${sort}`}
        load={(offset, signal) => loadWholesaleLines(id, sort, offset, signal)}
        empty={{ title: 'No lines', text: 'This invoice has no line margins.' }}
        controls={
          <SelectField
            aria-label="Margin order"
            value={sort}
            onChange={(event) => {
              setSort(event.target.value === '-margin' ? '-margin' : 'margin');
              setOffset(0);
            }}
          >
            <option value="margin">Lowest margin first</option>
            <option value="-margin">Highest margin first</option>
          </SelectField>
        }
      >
        {(line) => (
          <TableRow
            key={line.product?.id ?? line.msisdn ?? line.iccid}
            columns={LINE_COLUMNS}
            cells={{
              number: line.msisdn ?? '—',
              iccid: line.iccid ?? '—',
              rows: formatInt(line.invoiceLines),
              cost: money(line.cost),
              revenue: money(line.revenue),
              margin: money(line.margin),
            }}
          />
        )}
      </WholesaleTable>
    </section>
  );
}

function WholesaleTable<T>({
  title,
  columns,
  resourceKey,
  offset,
  onOffset,
  load,
  empty,
  controls,
  children,
}: {
  title: string;
  columns: Column[];
  resourceKey: string;
  offset: number;
  onOffset: (offset: number) => void;
  load: (offset: number, signal: AbortSignal) => Promise<Page<T>>;
  empty: { title: string; text: string; action?: ReactNode };
  controls?: ReactNode;
  children: (item: T) => ReactNode;
}) {
  const resource = useResource(`${resourceKey}:${offset}`, (signal) => load(offset, signal));
  return (
    <Panel title={title} right={controls}>
      <TableHead columns={columns} />
      <ListState
        resource={resource}
        columns={columns}
        errorTitle={`Failed to load ${title.toLowerCase()}`}
        empty={empty}
        count={(page) => page.items.length}
        rows={(page) => page.items}
        /* v8 ignore next -- @preserve unreachable: rows are the whole page, so a page with rows always shows them */
        onResetFilters={() => onOffset(0)}
      >
        {(items, page) => (
          <>
            {items.map(children)}
            <PageFooter
              offset={offset}
              shown={items.length}
              more={page.more}
              size={WHOLESALE_PAGE}
              onOffset={onOffset}
              ui="ui.wholesale.page"
            />
          </>
        )}
      </ListState>
    </Panel>
  );
}
