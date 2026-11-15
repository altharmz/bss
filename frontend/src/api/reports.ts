import type { components } from './generated/internal';
import { getJson } from './client';

// Prepaid KPIs by month (docs/decisions/reporting.md, issue #198): ours, not TMF.
export type KpiReport = components['schemas']['KpiReport'];
export type KpiMonth = components['schemas']['KpiMonth'];
export const KPI_REPORT = '/internal/v1/report/kpi';

/** "2026-10-20", 12 -> "2025-11": the first of the `months` months ending with the date's. */
export function firstMonth(businessDate: string, months: number): string {
  const [year, month] = businessDate.split('-').map(Number) as [number, number];
  const index = year * 12 + (month - 1) - (months - 1);
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
}

export function loadKpiReport(from: string, signal: AbortSignal) {
  return getJson<KpiReport>(`${KPI_REPORT}?from=${from}`, signal);
}
