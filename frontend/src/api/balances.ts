import { getAll, getJson } from './client';
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
  account: string | null,
  signal: AbortSignal,
): Promise<AccountTotal[]> {
  const path = account === null ? ACCUMULATED : `${ACCUMULATED}?partyAccount.id=${account}`;
  return (await getAll<AccumulatedBody>(path, signal)).map(accountTotal);
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

function bucketQuery(base: string, filter: BucketFilter): string {
  const query = [
    ...(filter.account !== null ? [`partyAccount.id=${filter.account}`] : []),
    ...(filter.status !== null ? [`status=${filter.status}`] : []),
  ];
  return query.length ? `${base}?${query.join('&')}` : base;
}

/** Every bucket, joined with the accumulated balance of its account by account id. */
export async function loadBucketRows(
  filter: BucketFilter,
  signal: AbortSignal,
): Promise<BucketRow[]> {
  const accountOnly =
    filter.account !== null ? `${ACCUMULATED}?partyAccount.id=${filter.account}` : ACCUMULATED;
  const [buckets, accumulated] = await Promise.all([
    getAll<BucketBody>(bucketQuery(PATHS.buckets, filter), signal),
    getAll<AccumulatedBody>(accountOnly, signal),
  ]);
  const byAccount = new Map<string, AccumulatedBody>();
  for (const acc of accumulated) {
    if (acc.partyAccount?.id) byAccount.set(acc.partyAccount.id, acc);
  }
  return buckets.map((bucket) => {
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
}

export interface BucketDetail {
  bucketId: string;
  status: string;
  remainingCents: number;
  reservedCents: number;
  expiry: string | null;
  totalCents: number | null;
}

/** The bucket's GET and, when joined, its account's accumulated balance GET. */
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
