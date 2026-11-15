import { useState } from 'react';
import { firstMonth, loadKpiReport, type KpiMonth, type KpiReport } from '../api/reports';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { formatInt, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { MoneyStrip, PageHeader, Panel, Segmented, type SegmentOption } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';

// Prepaid KPIs by month (docs/decisions/reporting.md, issue #198): lines, churn, billed
// revenue (tax excluded) by source and ARPU for the prepaid accounts in the UI currency.
// Everything is computed by the server; the UI only formats it, newest month first.

type Range = '6' | '12' | '24';
const RANGES: SegmentOption<Range>[] = [
  { key: '6', label: '6 months' },
  { key: '12', label: '12 months' },
  { key: '24', label: '24 months' },
];

const LINES: Column[] = [
  { key: 'month', label: 'Month', width: '1.2fr', mono: true },
  { key: 'opening', label: 'Opening', width: '1fr', align: 'right', mono: true },
  { key: 'adds', label: 'Gross adds', width: '1fr', align: 'right', mono: true },
  { key: 'disconnects', label: 'Disconnects', width: '1fr', align: 'right', mono: true },
  { key: 'net', label: 'Net adds', width: '1fr', align: 'right', mono: true },
  { key: 'closing', label: 'Closing', width: '1fr', align: 'right', mono: true },
  { key: 'churn', label: 'Churn', width: '0.9fr', align: 'right', mono: true },
  { key: 'revenue', label: 'Revenue', width: '1.3fr', align: 'right', mono: true },
  { key: 'arpu', label: 'ARPU', width: '1.1fr', align: 'right', mono: true },
];

const REVENUE: Column[] = [
  { key: 'month', label: 'Month', width: '1.2fr', mono: true },
  { key: 'plans', label: 'Plans', width: '1fr', align: 'right', mono: true },
  { key: 'addOns', label: 'Add-ons', width: '1fr', align: 'right', mono: true },
  { key: 'usage', label: 'Usage', width: '1fr', align: 'right', mono: true },
  { key: 'fees', label: 'Fees', width: '1fr', align: 'right', mono: true },
  { key: 'total', label: 'Total', width: '1.2fr', align: 'right', mono: true },
];

function signed(value: number): string {
  return value > 0 ? `+${formatInt(value)}` : formatInt(value);
}

function percent(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(2)}%`;
}

function money(value: { value: number } | null) {
  return value === null ? '—' : <Amount cents={toCents(value.value)} />;
}

function monthLabel(month: KpiMonth): string {
  return month.complete ? month.month : `${month.month} (to ${month.lastDay})`;
}

/** The figures of the latest complete month, or of the current one if none is complete. */
function Tiles({ report }: { report: KpiReport }) {
  const months = report.months;
  const month = [...months].reverse().find((m) => m.complete) ?? months[months.length - 1];
  if (month === undefined) return null;
  const sub = monthLabel(month);
  return (
    <MoneyStrip
      figures={[
        {
          ui: 'ui.reports.subscribers',
          label: 'Subscribers',
          value: formatInt(month.subscribers.closing),
          sub,
        },
        { ui: 'ui.reports.netAdds', label: 'Net adds', value: signed(month.subscribers.netAdds) },
        { ui: 'ui.reports.churn', label: 'Churn', value: percent(month.churnRate) },
        {
          ui: 'ui.reports.revenue',
          label: `Revenue (${report.currency})`,
          value: money(month.revenue.total),
        },
        { ui: 'ui.reports.arpu', label: `ARPU (${report.currency})`, value: money(month.arpu) },
      ]}
    />
  );
}

export function ReportsScreen() {
  useSetCrumbs([{ label: 'Reports' }], 'reports');
  const { businessDate, currency } = useSystemContext();
  const [range, setRange] = useState<Range>('12');
  const from = firstMonth(businessDate, Number(range));
  const report = useResource(`kpi:${from}`, (signal) => loadKpiReport(from, signal));
  const newest = (data: KpiReport) => [...data.months].reverse();
  const empty = { title: 'No months', text: 'The report has no months in this range.' };
  return (
    <div className="bss-screen" data-ui="ui.reports">
      <PageHeader
        title="Prepaid KPIs"
        meta={`Prepaid accounts in ${currency}. Revenue is billed revenue, tax excluded.`}
        filters={<Segmented label="Range" options={RANGES} value={range} onChange={setRange} />}
      />
      {report.status === 'ok' && <Tiles report={report.data} />}
      <Panel title="Subscribers by month" subtitle="Newest first">
        <TableHead columns={LINES} />
        <ListState
          resource={report}
          columns={LINES}
          errorTitle="Failed to load the report"
          empty={empty}
          count={(data) => data.months.length}
          rows={newest}
          onResetFilters={() => setRange('12')}
        >
          {(rows) =>
            rows.map((m) => (
              <TableRow
                key={m.month}
                columns={LINES}
                cells={{
                  month: monthLabel(m),
                  opening: formatInt(m.subscribers.opening),
                  adds: formatInt(m.subscribers.grossAdds),
                  disconnects: formatInt(m.subscribers.disconnects),
                  net: signed(m.subscribers.netAdds),
                  closing: formatInt(m.subscribers.closing),
                  churn: percent(m.churnRate),
                  revenue: money(m.revenue.total),
                  arpu: money(m.arpu),
                }}
              />
            ))
          }
        </ListState>
      </Panel>
      <Panel title="Revenue by source" subtitle={`Tax excluded, ${currency}`}>
        <TableHead columns={REVENUE} />
        <ListState
          resource={report}
          columns={REVENUE}
          errorTitle="Failed to load the report"
          empty={empty}
          count={(data) => data.months.length}
          rows={newest}
          onResetFilters={() => setRange('12')}
        >
          {(rows) =>
            rows.map((m) => (
              <TableRow
                key={m.month}
                columns={REVENUE}
                cells={{
                  month: monthLabel(m),
                  plans: money(m.revenue.plans),
                  addOns: money(m.revenue.addOns),
                  usage: money(m.revenue.usage),
                  fees: money(m.revenue.fees),
                  total: money(m.revenue.total),
                }}
              />
            ))
          }
        </ListState>
      </Panel>
    </div>
  );
}
