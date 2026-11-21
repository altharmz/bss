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
import { Amount } from '../ui/Amount';
import { Badge, Button, KeyValue, Panel, SelectField, StatusPill, Tag } from '../ui/kit';
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
  { key: 'status', label: 'Status', width: '110px' },
  { key: 'price', label: 'Applied price', width: '220px', align: 'right', mono: true },
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
  const { kind, unit, used, included, remaining, percent, thresholds, pool } = allowance;
  // a pooled line's domestic data is the account pool's (issue 379): say so on the bar
  const label = allowance.pooled ? `${KIND[kind] ?? kind} · shared pool` : (KIND[kind] ?? kind);
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
      {allowance.pooled && pool !== null && (
        <div className="bss-secondary" data-ui="ui.subscriber.usage.pooled">
          The account&apos;s data pool of {formatInt(pool.members)}{' '}
          {pool.members === 1 ? 'line' : 'lines'}, {pool.startDate} – {pool.endDate} (end excluded)
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
        summary.allowances.map((a) => <AllowanceBar key={`${a.zone}-${a.kind}`} allowance={a} />)
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
        count={(data) => data.items.length}
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
                  status: (
                    <span data-ui="ui.subscriber.usage.status">
                      <StatusPill kind="usage" status={r.status} />
                    </span>
                  ),
                  price:
                    r.appliedPrice === null ? (
                      <span className="bss-dash">—</span>
                    ) : (
                      <div data-ui="ui.subscriber.usage.appliedPrice">
                        <Amount cents={r.appliedPrice.cents} currency={r.appliedPrice.currency} />
                        <div className="bss-secondary">
                          {r.appliedPrice.name} · per {r.appliedPrice.unit}
                        </div>
                      </div>
                    ),
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
                left={`Showing ${formatInt(offset + 1)}–${formatInt(offset + rows.length)}`}
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
                      disabled={!records.data.more}
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

/** The account's data pool (issue 379), from the usage summary of its first pooled line:
 * the summaries are read line by line until one has a pooled entry, so an account without
 * a pool shows nothing. */
async function loadPool(lines: AccountProduct[], signal: AbortSignal): Promise<Allowance | null> {
  for (const line of lines) {
    const summary = await loadUsageSummary(line.id, signal);
    const pooled = summary.allowances.find((a) => a.pooled && a.pool !== null);
    if (pooled !== undefined) return pooled;
  }
  return null;
}

export function DataPoolPanel({
  products,
  version,
}: {
  products: AccountProduct[];
  version: number;
}) {
  const plans = products.filter((p) => p.offeringKind === 'plan');
  const pool = useResource(
    `data-pool:${plans.map((p) => p.id).join(',')}:${version}`,
    (signal) => loadPool(plans, signal),
    true,
  );
  if (pool.status === 'loading' || (pool.status === 'ok' && pool.data === null)) return null;
  if (pool.status === 'error') {
    // the same summary request also fails in the Allowance panel below, which has the Retry
    return (
      <Note tone="negative" data-ui="ui.subscriber.dataPool">
        Data pool unavailable: {pool.error.message}
      </Note>
    );
  }
  const a = pool.data;
  if (a === null || a.pool === null) return null;
  return (
    <section data-ui="ui.subscriber.dataPool">
      <Panel title="Data pool" subtitle="Shared by the account's pooled lines">
        <KeyValue
          items={[
            [
              'Used',
              a.included <= 0
                ? `${formatQuantity(a.used, a.unit)} · nothing included`
                : `${formatQuantity(a.used, a.unit)} of ${formatQuantity(a.included, a.unit)} (${a.percent ?? 0}%)`,
            ],
            ['Left', formatQuantity(a.remaining, a.unit)],
            ['Members', `${formatInt(a.pool.members)} ${a.pool.members === 1 ? 'line' : 'lines'}`],
            ['Period', `${a.pool.startDate} – ${a.pool.endDate} (end excluded)`],
          ]}
        />
        <p className="bss-secondary">
          Every pooled line&apos;s domestic data counts against one allowance per billing month;
          voice, SMS and roaming stay per line.
        </p>
      </Panel>
    </section>
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
      <DataPoolPanel products={products} version={version} />
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
