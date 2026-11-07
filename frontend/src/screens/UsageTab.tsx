import { useState, type CSSProperties } from 'react';
import { KIND_LABEL, type AccountProduct } from '../api/bss';
import {
  loadUsageRecords,
  loadUsageSummary,
  USAGE_PAGE,
  type Allowance,
  type UsageRecord,
  type UsageSummary,
} from '../api/usage';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { shortId } from '../format/ids';
import { formatInt } from '../format/money';
import { Badge, Button, KeyValue, Panel, SelectField, Tag } from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';

// The Usage tab of a prepaid subscriber (docs/decisions/usage.md): the plan period's
// allowance per kind from the internal summary, and the line's network records from TMF635.
// The server gives every figure (used, included, remaining, percent, marks, throttled).

const KIND: Record<string, string> = { data: 'Data', voice: 'Voice', sms: 'SMS' };
const RECORD_COLUMNS: Column[] = [
  { key: 'time', label: 'Time', width: '172px', mono: true },
  { key: 'kind', label: 'Kind', width: '90px' },
  { key: 'quantity', label: 'Quantity', width: '1fr', align: 'right', mono: true },
  { key: 'flags', label: 'Flags', width: '180px' },
];

/** Bytes, seconds and messages as a person reads them. */
export function formatQuantity(value: number, unit: string): string {
  if (unit === 'bytes') {
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${formatInt(value)} B`;
  }
  if (unit === 'seconds') {
    return value >= 60 ? `${formatInt(Math.floor(value / 60))} min ${value % 60} s` : `${value} s`;
  }
  return `${formatInt(value)} ${value === 1 ? 'message' : 'messages'}`;
}

/** Lines that have usage: a plan or a pay-as-you-go product (add-ons raise the plan's). */
export function usageLines(products: AccountProduct[]): AccountProduct[] {
  return products.filter((p) => p.offeringKind === 'plan' || p.offeringKind === 'payAsYouGo');
}

function AllowanceBar({ allowance }: { allowance: Allowance }) {
  const { kind, unit, used, included, remaining, percent, thresholds } = allowance;
  const label = KIND[kind] ?? kind;
  const none = included <= 0;
  const fill = none ? 0 : Math.min(percent ?? 0, 100);
  return (
    <div className="usage-allowance" data-ui={`ui.subscriber.usage.${kind}`}>
      <div className="usage-allowance-head">
        <strong>{label}</strong>
        <span className="num">
          {none
            ? `${formatQuantity(used, unit)} used · nothing included`
            : `${formatQuantity(used, unit)} of ${formatQuantity(included, unit)} · ${formatQuantity(remaining, unit)} left`}
        </span>
      </div>
      {!none && (
        <div
          className="usage-bar"
          role="progressbar"
          aria-label={`${label} used`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={fill}
          aria-valuetext={`${percent ?? 0}% used`}
        >
          <div
            className={`usage-bar-fill${thresholds.includes(100) ? ' full' : thresholds.includes(80) ? ' warn' : ''}`}
            style={{ '--usage-fill': `${fill}%` } as CSSProperties}
          />
          <span className="usage-mark usage-mark-80" aria-hidden="true" />
        </div>
      )}
      {!none && (
        <div className="usage-marks bss-secondary">
          {percent ?? 0}% used
          {thresholds.includes(80) && <Badge kind="info">80% reached</Badge>}
          {thresholds.includes(100) && <Badge kind="failed">100% reached</Badge>}
        </div>
      )}
    </div>
  );
}

function Summary({ summary }: { summary: UsageSummary }) {
  const pay = summary.payAsYouGo;
  return (
    <>
      <div className="usage-flags">
        {summary.throttled && (
          <span data-ui="ui.subscriber.usage.throttled">
            <Badge kind="failed">Throttled</Badge>
          </span>
        )}
        {pay?.barred && (
          <span data-ui="ui.subscriber.usage.barred">
            <Badge kind="failed">Barred</Badge>
          </span>
        )}
      </div>
      {summary.period === null ? (
        <Note tone="info">No plan period covers today, so there is no allowance to show.</Note>
      ) : (
        summary.allowances.map((a) => <AllowanceBar key={a.kind} allowance={a} />)
      )}
      {pay !== null && (
        <KeyValue
          items={[
            ['Balance', `${pay.balance.value} ${pay.balance.unit}`],
            ['Unrated records', formatInt(pay.unratedRecords)],
          ]}
        />
      )}
    </>
  );
}

function Records({ product, timeZone }: { product: string; timeZone: string }) {
  const [offset, setOffset] = useState(0);
  const records = useResource(
    `usage-records:${product}:${offset}`,
    (signal) => loadUsageRecords(product, offset, signal),
    true,
  );
  return (
    <Panel title="Usage records" subtitle="Newest first">
      <TableHead columns={RECORD_COLUMNS} />
      <ListState
        resource={records}
        columns={RECORD_COLUMNS}
        errorTitle="Failed to load usage records"
        empty={{ title: 'No usage', text: 'The network has reported no usage for this line.' }}
        total={(data) => data.total}
        rows={(data) => data.items}
        onResetFilters={() => {}}
      >
        {(rows: UsageRecord[]) => (
          <>
            {rows.map((r) => (
              <TableRow
                key={r.id}
                columns={RECORD_COLUMNS}
                cells={{
                  time: `${businessDate(r.time, timeZone)} ${clockTime(r.time, timeZone)}`,
                  kind: KIND[r.kind] ?? r.kind,
                  quantity: formatQuantity(r.quantity, r.unit),
                  flags: (
                    <span className="usage-record-flags">
                      {r.roaming && <Tag>Roaming</Tag>}
                      {r.throttled && <Tag>Throttled</Tag>}
                      {r.status === 'rejected' && <Tag>No line</Tag>}
                    </span>
                  ),
                }}
              />
            ))}
            {records.status === 'ok' && (
              <TableFooter
                left={`Showing ${formatInt(offset + 1)}–${formatInt(offset + rows.length)} of ${formatInt(records.data.total)}`}
                right={
                  <span className="bss-table-pager">
                    <Button
                      small
                      data-ui="ui.subscriber.usage.previous"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - USAGE_PAGE))}
                    >
                      Previous
                    </Button>
                    <Button
                      small
                      data-ui="ui.subscriber.usage.next"
                      disabled={offset + USAGE_PAGE >= records.data.total}
                      onClick={() => setOffset(offset + USAGE_PAGE)}
                    >
                      Next
                    </Button>
                  </span>
                }
              />
            )}
          </>
        )}
      </ListState>
    </Panel>
  );
}

/** The tab body: pick a line, then its period summary and records. Loaded only while the
 * tab is shown, so the card makes no usage request for a subscriber who never opens it. */
export function UsageTab({ products, version }: { products: AccountProduct[]; version: number }) {
  const context = useSystemContext();
  const lines = usageLines(products);
  const [picked, setPicked] = useState<string | null>(null);
  const product = lines.find((p) => p.id === picked) ?? lines[0];
  const summary = useResource(
    `usage-summary:${product?.id ?? 'none'}:${version}`,
    (signal) =>
      product === undefined ? Promise.resolve(null) : loadUsageSummary(product.id, signal),
    true,
  );
  if (product === undefined) {
    return (
      <Note tone="info">This account has no plan or pay-as-you-go line, so there is no usage.</Note>
    );
  }
  return (
    <section data-ui="ui.subscriber.usage">
      <Panel
        title="Allowance"
        subtitle={
          summary.status === 'ok' && summary.data?.period
            ? `${summary.data.period.startDate} – ${summary.data.period.endDate} (end excluded)`
            : undefined
        }
        right={
          lines.length > 1 ? (
            <SelectField
              aria-label="Line"
              value={product.id}
              onChange={(e) => setPicked(e.target.value)}
            >
              {lines.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.productOffering.name} · PRD-{shortId(p.id)}
                </option>
              ))}
            </SelectField>
          ) : (
            <span className="bss-secondary">
              {product.offeringKind !== undefined && KIND_LABEL[product.offeringKind]}
            </span>
          )
        }
      >
        {summary.status === 'loading' && <p className="bss-secondary">Loading usage…</p>}
        {summary.status === 'error' && (
          <ErrorBlock
            title="Failed to load the usage summary"
            error={summary.error}
            onRetry={summary.retry}
          />
        )}
        {summary.status === 'ok' && summary.data !== null && <Summary summary={summary.data} />}
      </Panel>
      <Records key={product.id} product={product.id} timeZone={context.timeZone} />
    </section>
  );
}
