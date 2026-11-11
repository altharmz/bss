import { getAll, getJson, getPage } from './client';
import { PATHS } from './bss';
import { toCents } from '../format/money';

// The Balances screen's buckets tab (docs/decisions/frontend.md, slice 22): TMF654 reads
// only. Postpaid accounts have no bucket, so they are never listed.

export const ACCUMULATED = '/tmf-api/prepayBalanceManagement/v4/accumulatedBalance';

export const BUCKET_STATUSES = ['active', 'suspended', 'expired'] as const;

interface Ref {
  id?: string;
  name?: string | null;
}

interface Quantity {
  amount?: number;
  units?: string;
}

interface BucketBody {
  id?: string;
  status?: string;
  remainingValue?: Quantity;
  reservedValue?: Quantity;
  partyAccount?: Ref;
  validFor?: { endDateTime?: string };
}

interface AccumulatedBody {
  id?: string;
  totalBalance?: Quantity;
  partyAccount?: Ref;
}

export interface AccountTotal {
  id: string;
  account: { id: string; name: string | null };
  cents: number;
  currency: string;
}

function accountTotal(body: AccumulatedBody): AccountTotal {
  return {
    id: need(body.id, 'accumulated balance id'),
    account: {
      id: need(body.partyAccount?.id, 'accumulated balance partyAccount'),
      name: body.partyAccount?.name ?? null,
    },
    cents: toCents(need(body.totalBalance?.amount, 'totalBalance')),
    currency: need(body.totalBalance?.units, 'totalBalance units'),
  };
}

export async function loadAccountTotals(
  account: string,
  signal: AbortSignal,
): Promise<AccountTotal[]> {
  return (await getAll<AccumulatedBody>(`${ACCUMULATED}?partyAccount.id=${account}`, signal)).map(
    accountTotal,
  );
}

export const BALANCE_PAGE = 50;

type Paged<T> = { items: T[]; more: boolean; offset: number };

/** One page of account totals (#314); one account's when `account` is set. */
export async function loadAccountTotalPage(
  account: string | null,
  offset: number,
  signal: AbortSignal,
): Promise<Paged<AccountTotal>> {
  const query = new URLSearchParams();
  if (account !== null) query.set('partyAccount.id', account);
  query.set('offset', String(offset));
  query.set('limit', String(BALANCE_PAGE));
  const page = await getPage<AccumulatedBody>(`${ACCUMULATED}?${query}`, signal);
  return { items: page.items.map(accountTotal), more: page.more, offset };
}

export async function loadAccountTotal(id: string, signal: AbortSignal): Promise<AccountTotal> {
  return accountTotal(await getJson<AccumulatedBody>(`${ACCUMULATED}/${id}`, signal));
}

export interface BucketRow {
  bucketId: string;
  accumulatedId: string | null;
  account: { id: string; name: string | null };
  status: string;
  remainingCents: number;
  reservedCents: number;
  /** The account's accumulated balance (TMF654), null when none was joined. */
  totalCents: number | null;
}

export interface BucketFilter {
  account: string | null;
  status: string | null;
}

function need<T>(value: T | undefined | null, what: string): T {
  if (value === undefined || value === null) throw new Error(`the API sent no ${what}`);
  return value;
}

/**
 * One page of buckets (#314), each joined with the accumulated balance of its account:
 * one filtered read per account on the page, not every accumulated balance.
 */
export async function loadBucketRows(
  filter: BucketFilter,
  offset: number,
  signal: AbortSignal,
): Promise<Paged<BucketRow>> {
  const query = new URLSearchParams();
  if (filter.account !== null) query.set('partyAccount.id', filter.account);
  if (filter.status !== null) query.set('status', filter.status);
  query.set('offset', String(offset));
  query.set('limit', String(BALANCE_PAGE));
  const page = await getPage<BucketBody>(`${PATHS.buckets}?${query}`, signal);
  const accounts = [
    ...new Set(page.items.map((bucket) => need(bucket.partyAccount?.id, 'bucket partyAccount'))),
  ];
  const totals = await Promise.all(
    accounts.map((id) =>
      getPage<AccumulatedBody>(`${ACCUMULATED}?partyAccount.id=${id}&limit=1`, signal),
    ),
  );
  const byAccount = new Map<string, AccumulatedBody>();
  totals.forEach((total, i) => {
    const first = total.items[0];
    if (first) byAccount.set(accounts[i] as string, first);
  });
  const items = page.items.map((bucket) => {
    const account = need(bucket.partyAccount?.id, 'bucket partyAccount');
    const acc = byAccount.get(account);
    return {
      bucketId: need(bucket.id, 'bucket id'),
      accumulatedId: acc?.id ?? null,
      account: { id: account, name: bucket.partyAccount?.name ?? null },
      status: need(bucket.status, 'bucket status'),
      remainingCents: toCents(need(bucket.remainingValue?.amount, 'remainingValue')),
      reservedCents: toCents(need(bucket.reservedValue?.amount, 'reservedValue')),
      totalCents: acc?.totalBalance?.amount === undefined ? null : toCents(acc.totalBalance.amount),
    };
  });
  return { items, more: page.more, offset };
}

export interface BucketDetail {
  bucketId: string;
  status: string;
  remainingCents: number;
  reservedCents: number;
  expiry: string | null;
  totalCents: number | null;
}

export async function loadBucketDetail(row: BucketRow, signal: AbortSignal): Promise<BucketDetail> {
  const [bucket, accumulated] = await Promise.all([
    getJson<BucketBody>(`${PATHS.buckets}/${row.bucketId}`, signal),
    row.accumulatedId === null
      ? Promise.resolve(null)
      : getJson<AccumulatedBody>(`${ACCUMULATED}/${row.accumulatedId}`, signal),
  ]);
  return {
    bucketId: row.bucketId,
    status: need(bucket.status, 'bucket status'),
    remainingCents: toCents(need(bucket.remainingValue?.amount, 'remainingValue')),
    reservedCents: toCents(need(bucket.reservedValue?.amount, 'reservedValue')),
    expiry: bucket.validFor?.endDateTime ?? null,
    totalCents:
      accumulated?.totalBalance?.amount === undefined
        ? null
        : toCents(accumulated.totalBalance.amount),
  };
}
