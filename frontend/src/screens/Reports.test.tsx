import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { firstMonth } from '../api/reports';
import { ReportsScreen } from './Reports';
import { CrumbsProvider, SystemContextProvider } from '../app/context';
import { RouterProvider } from '../app/router';
import { CONTEXT, json, stubFetch, tmfError } from '../test/fetch';
import { expectUi } from '../test/expectUi';

const usd = (value: number) => ({ unit: 'USD', value });
const revenue = (plans: number, addOns: number, usage: number, fees: number) => ({
  total: usd(plans + addOns + usage + fees),
  plans: usd(plans),
  addOns: usd(addOns),
  usage: usd(usage),
  fees: usd(fees),
});
const SEPTEMBER = {
  month: '2026-09',
  firstDay: '2026-09-01',
  lastDay: '2026-09-30',
  complete: true,
  subscribers: { opening: 3, grossAdds: 1, disconnects: 2, netAdds: -1, closing: 2, average: 2.5 },
  churnRate: 66.67,
  revenue: revenue(20, 5, 0, 2.5),
  arpu: usd(11),
};
const OCTOBER = {
  month: '2026-10',
  firstDay: '2026-10-01',
  lastDay: '2026-10-01',
  complete: false,
  subscribers: { opening: 2, grossAdds: 1, disconnects: 0, netAdds: 1, closing: 3, average: 2.5 },
  churnRate: 0,
  revenue: revenue(0, 0, 1.25, 0),
  arpu: usd(0.5),
};
const report = (from: string, months: object[]) => ({
  currency: 'USD',
  businessDate: '2026-10-01',
  from,
  to: '2026-10',
  months,
});
const TWELVE = 'GET /internal/v1/report/kpi?from=2025-11';
const SIX = 'GET /internal/v1/report/kpi?from=2026-05';

function screenOf() {
  return render(
    <RouterProvider>
      <SystemContextProvider value={CONTEXT}>
        <CrumbsProvider>
          <ReportsScreen />
        </CrumbsProvider>
      </SystemContextProvider>
    </RouterProvider>,
  );
}

describe('prepaid KPI report', () => {
  it('shows the latest complete month as tiles and every month newest first', async () => {
    stubFetch({ [TWELVE]: () => json(report('2025-11', [SEPTEMBER, OCTOBER])) });
    screenOf();
    expectUi('ui.reports');
    const lines = await screen.findAllByText('2026-10 (to 2026-10-01)');
    expect(lines).toHaveLength(2); // both tables
    const tile = (ui: string) => document.querySelector(`[data-ui="${ui}"]`)?.textContent;
    expect(tile('ui.reports.subscribers')).toBe('Subscribers22026-09'); // September's closing
    expect(tile('ui.reports.netAdds')).toBe('Net adds−1');
    expect(tile('ui.reports.arpu')).toBe('ARPU (USD)11.00');
    expect(screen.getAllByText('66.67%')).toHaveLength(2); // tile and row
    const rows = screen.getAllByRole('row').map((row) => row.textContent ?? '');
    const months = rows.filter((text) => text.startsWith('2026-'));
    expect(months[0]).toBe('2026-10 (to 2026-10-01)210+130.00%1.250.50');
    expect(months[1]).toBe('2026-09312−1266.67%27.5011.00');
    expect(months[3]).toBe('2026-0920.005.000.002.5027.50');
  });

  it('asks for six months and shows a load error with Retry', async () => {
    const { calls } = stubFetch({
      [TWELVE]: () => json(report('2025-11', [OCTOBER])),
      [SIX]: () => tmfError(500, 'boom'),
    });
    screenOf();
    await screen.findAllByText('2026-10 (to 2026-10-01)');
    fireEvent.click(screen.getByRole('button', { name: '6 months' }));
    expect(await screen.findAllByText('Failed to load the report')).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Retry' })).toHaveLength(2);
    expect(calls).toEqual([TWELVE, SIX]);
  });

  it('counts the first month of a range back from the business date', () => {
    expect(firstMonth('2026-10-20', 12)).toBe('2025-11');
    expect(firstMonth('2026-01-05', 1)).toBe('2026-01');
    expect(firstMonth('2026-12-31', 24)).toBe('2025-01');
  });
});
