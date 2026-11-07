import type { components as Internal } from './generated/internal';
import { getJson, getPage } from './client';

// Usage of a prepaid line (docs/decisions/usage.md): the period summary is ours
// (`GET /internal/v1/usage/summary`), the records are TMF635 `GET /productUsage`.
// Read-only; nothing is computed here, the server gives used, included and the marks.

export type UsageSummary = Internal['schemas']['UsageSummary'];
export type Allowance = UsageSummary['allowances'][number];

export const USAGE_PAGE = 25;
const SUMMARY = '/internal/v1/usage/summary';
const USAGE = '/tmf-api/productUsageManagement/v5/productUsage';

export function loadUsageSummary(productId: string, signal: AbortSignal): Promise<UsageSummary> {
  return getJson<UsageSummary>(`${SUMMARY}?product.id=${encodeURIComponent(productId)}`, signal);
}

interface Characteristic {
  name?: string;
  value?: unknown;
}

interface UsageBody {
  id?: string;
  status?: string;
  usageType?: string;
  usageCharacteristic?: Characteristic[];
}

/** One network record as the table shows it. */
export interface UsageRecord {
  id: string;
  kind: string;
  quantity: number;
  unit: string;
  time: string;
  roaming: boolean;
  throttled: boolean;
  status: string;
}

function pick(body: UsageBody, name: string): unknown {
  return body.usageCharacteristic?.find((c) => c.name === name)?.value;
}

export function toRecord(body: UsageBody): UsageRecord {
  if (typeof body.id !== 'string' || typeof body.usageType !== 'string') {
    throw new Error('the API sent a usage without an id or a type');
  }
  const quantity = pick(body, 'quantity');
  const time = pick(body, 'eventTime');
  if (typeof quantity !== 'number' || typeof time !== 'string') {
    throw new Error('the API sent a usage without a quantity or a time');
  }
  return {
    id: body.id,
    kind: body.usageType,
    quantity,
    unit: String(pick(body, 'unit') ?? ''),
    time,
    roaming: pick(body, 'roaming') === true,
    throttled: pick(body, 'throttled') === true,
    status: body.status ?? '',
  };
}

/** One page of a line's records, newest first; `total` is every record (X-Total-Count). */
export async function loadUsageRecords(productId: string, offset: number, signal: AbortSignal) {
  const page = await getPage<UsageBody>(
    `${USAGE}?product.id=${encodeURIComponent(productId)}&offset=${offset}&limit=${USAGE_PAGE}`,
    signal,
  );
  return { items: page.items.map(toRecord), total: page.total };
}
