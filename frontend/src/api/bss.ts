// What the screens load and send, mapped from the generated spec types onto exactly what
// the UI needs. The TMF types make every attribute optional; our API always sends the
// ones read here, so a missing one is an error (shown in the error block), never a
// silently blank cell.

import { toCents } from '../format/money';
import { addDays, zonedDateTime } from '../format/dates';
import {
  AMOUNT,
  getAll,
  getJson,
  getPage,
  jsonWithAmount,
  patchJson,
  postJson,
  request,
  type Page,
} from './client';
import type { components as Internal } from './generated/internal';
import type { components as T620 } from './generated/tmf620';
import type { components as T622 } from './generated/tmf622';
import type { components as T629 } from './generated/tmf629';
import type { components as T637 } from './generated/tmf637';
import type { components as T666 } from './generated/tmf666';

export const PATHS = {
  accounts: '/tmf-api/accountManagement/v5/billingAccount',
  customers: '/tmf-api/customerManagement/v5/customer',
  products: '/tmf-api/productInventory/v5/product',
  offerings: '/tmf-api/productCatalogManagement/v5/productOffering',
  prices: '/tmf-api/productCatalogManagement/v5/productOfferingPrice',
  orders: '/tmf-api/productOrderingManagement/v5/productOrder',
  internal: '/internal/v1/billingAccount',
  runs: '/internal/v1/billingRun',
};

export type AccountProduct = Internal['schemas']['AccountProduct'];
export type Charge = Internal['schemas']['Charge'];
export type Transaction = Internal['schemas']['Transaction'];
export type FirstCharge = Internal['schemas']['FirstCharge'];

function need<T>(value: T | undefined | null, what: string): T {
  if (value === undefined || value === null) throw new Error(`the API sent no ${what}`);
  return value;
}

// --- billing accounts (a subscriber row is a billing account, D1) ---

/** TMF666 BillingAccount plus the BssBillingAccount extension this service sends. */
type BillingAccountBody = T666['schemas']['BillingAccount'] & {
  billCycleDay?: number;
  currency?: string;
};

export type AccountState = 'Active' | 'Suspended' | 'Closed';

export interface Account {
  id: string;
  name: string;
  state: AccountState;
  customerId: string;
  customerName: string;
  cycleDay: number;
  currency: string;
  balanceCents: number; // positive = in credit
  creditLimitCents: number | null;
}

export function toAccount(body: BillingAccountBody): Account {
  const customer = need(body.relatedParty?.[0]?.partyOrPartyRole, 'relatedParty customer');
  const balance = need(body.accountBalance?.[0]?.amount?.value, 'accountBalance');
  const limit = body.creditLimit?.value;
  return {
    id: need(body.id, 'id'),
    name: need(body.name, 'name'),
    state: need(body.state, 'state') as AccountState,
    customerId: need(customer.id, 'customer id'),
    customerName: need(customer.name, 'customer name'),
    cycleDay: need(body.billCycleDay, 'billCycleDay'),
    currency: need(body.currency, 'currency'),
    balanceCents: toCents(balance),
    creditLimitCents: limit === undefined ? null : toCents(limit),
  };
}

export async function loadAccounts(signal: AbortSignal): Promise<Account[]> {
  return (await getAll<BillingAccountBody>(PATHS.accounts, signal)).map(toAccount);
}

export async function loadAccount(id: string, signal: AbortSignal): Promise<Account> {
  return toAccount(await getJson<BillingAccountBody>(`${PATHS.accounts}/${id}`, signal));
}

/** Active products per billing account: one product list (status=active), grouped here
 * - no request per row (D4). */
export async function loadActiveProductCounts(signal: AbortSignal): Promise<Map<string, number>> {
  type Product = T637['schemas']['Product'];
  const products = await getAll<Product>(
    `${PATHS.products}?status=active&fields=billingAccount`,
    signal,
  );
  const counts = new Map<string, number>();
  for (const product of products) {
    const account = need(product.billingAccount?.id, 'product billingAccount');
    counts.set(account, (counts.get(account) ?? 0) + 1);
  }
  return counts;
}

export interface Subscribers {
  accounts: Account[];
  activeProducts: Map<string, number>;
}

export async function loadSubscribers(signal: AbortSignal): Promise<Subscribers> {
  const [accounts, activeProducts] = await Promise.all([
    loadAccounts(signal),
    loadActiveProductCounts(signal),
  ]);
  return { accounts, activeProducts };
}

// --- customer (its status is shown on the card, D1) ---

export async function loadCustomerStatus(id: string, signal: AbortSignal): Promise<string> {
  const customer = await getJson<T629['schemas']['Customer']>(`${PATHS.customers}/${id}`, signal);
  return need(customer.status, 'customer status');
}

// --- the card's internal views ---

export function loadProducts(accountId: string, signal: AbortSignal): Promise<AccountProduct[]> {
  return getJson<AccountProduct[]>(`${PATHS.internal}/${accountId}/products`, signal);
}

export function loadCharges(accountId: string, signal: AbortSignal): Promise<Charge[]> {
  return getJson<Charge[]>(`${PATHS.internal}/${accountId}/charges?limit=20`, signal);
}

export function loadTransactions(accountId: string, signal: AbortSignal): Promise<Transaction[]> {
  return getJson<Transaction[]>(`${PATHS.internal}/${accountId}/transactions?limit=20`, signal);
}

export function loadFirstCharge(
  accountId: string,
  offeringId: string,
  startDate: string,
  signal: AbortSignal,
): Promise<FirstCharge> {
  const query = `productOffering.id=${encodeURIComponent(offeringId)}&startDate=${startDate}`;
  return getJson<FirstCharge>(`${PATHS.internal}/${accountId}/firstCharge?${query}`, signal);
}

// --- offerings for Add product ---

type OfferingBody = T620['schemas']['ProductOffering'];
type PriceBody = T620['schemas']['ProductOfferingPrice'];

export interface Offering {
  id: string;
  name: string;
  code: string | null;
  specification: string;
  priceCents: number | null; // display only; the charge itself comes from the preview
}

/** The recurring price valid at `now` in `currency`, for display in lists. What is
 * charged is never computed here: the first-charge preview asks the backend. */
function displayPrice(
  offering: OfferingBody,
  prices: Map<string, PriceBody>,
  now: string,
  currency: string,
) {
  for (const ref of offering.productOfferingPrice ?? []) {
    const price = ref.id === undefined ? undefined : prices.get(ref.id);
    const value = price?.price?.value;
    const from = price?.validFor?.startDateTime;
    const to = price?.validFor?.endDateTime;
    if (
      value !== undefined &&
      price?.price?.unit === currency &&
      from !== undefined &&
      Date.parse(from) <= Date.parse(now) &&
      (to === undefined || Date.parse(now) < Date.parse(to))
    ) {
      return toCents(value);
    }
  }
  return null;
}

export async function loadSellableOfferings(
  now: string,
  currency: string,
  signal: AbortSignal,
): Promise<Offering[]> {
  const [offerings, prices] = await Promise.all([
    getAll<OfferingBody>(`${PATHS.offerings}?lifecycleStatus=Active&isSellable=true`, signal),
    getAll<PriceBody>(`${PATHS.prices}?priceType=recurring`, signal),
  ]);
  const byId = new Map(prices.map((price) => [need(price.id, 'price id'), price]));
  return offerings.map((offering) => ({
    id: need(offering.id, 'offering id'),
    name: need(offering.name, 'offering name'),
    code: offering.externalIdentifier?.[0]?.id ?? null,
    specification: offering.productSpecification?.name ?? '',
    priceCents: displayPrice(offering, byId, now, currency),
  }));
}

// --- the catalog (TMF620) ---

export type Lifecycle = 'In Design' | 'Active' | 'Retired';
type SpecBody = T620['schemas']['ProductSpecification'];

export interface CatalogPrice {
  id: string;
  cents: number;
  currency: string;
  from: string; // instant
  to: string | null; // instant, exclusive; null = open-ended
}

export interface CatalogOffering {
  id: string;
  name: string;
  code: string | null;
  status: Lifecycle;
  specification: string;
  validFrom: string; // instant
  validTo: string | null; // instant, exclusive
  prices: CatalogPrice[]; // recurring, attached to this offering
}

export interface Specification {
  id: string;
  name: string;
}

export interface Catalog {
  offerings: CatalogOffering[];
  specifications: Specification[];
}

export async function loadCatalog(signal: AbortSignal): Promise<Catalog> {
  const [offerings, specs, prices] = await Promise.all([
    getAll<OfferingBody>(PATHS.offerings, signal),
    getAll<SpecBody>('/tmf-api/productCatalogManagement/v5/productSpecification', signal),
    getAll<PriceBody>(`${PATHS.prices}?priceType=recurring`, signal),
  ]);
  const priceById = new Map(prices.map((price) => [need(price.id, 'price id'), price]));
  return {
    specifications: specs.map((spec) => ({
      id: need(spec.id, 'spec id'),
      name: need(spec.name, 'spec name'),
    })),
    offerings: offerings.map((offering) => ({
      id: need(offering.id, 'offering id'),
      name: need(offering.name, 'offering name'),
      code: offering.externalIdentifier?.[0]?.id ?? null,
      status: need(offering.lifecycleStatus, 'lifecycleStatus') as Lifecycle,
      specification: offering.productSpecification?.name ?? '',
      validFrom: need(offering.validFor?.startDateTime, 'validFor.startDateTime'),
      validTo: offering.validFor?.endDateTime ?? null,
      prices: (offering.productOfferingPrice ?? []).flatMap((ref) => {
        const price = ref.id === undefined ? undefined : priceById.get(ref.id);
        if (price === undefined) return []; // not recurring (or not visible): not listed
        return [
          {
            id: need(price.id, 'price id'),
            cents: toCents(need(price.price?.value, 'price value')),
            currency: need(price.price?.unit, 'price unit'),
            from: need(price.validFor?.startDateTime, 'price validFor.startDateTime'),
            to: price.validFor?.endDateTime ?? null,
          },
        ];
      }),
    })),
  };
}

/** The price the catalog shows: the one valid at `now` in `currency`, else the next one
 * that starts later (shown with its start). Display only. */
export function shownPrice(
  prices: CatalogPrice[],
  now: string,
  currency: string,
): { cents: number; from: string | null } | null {
  const at = Date.parse(now);
  const mine = prices.filter((p) => p.currency === currency);
  const current = mine.find(
    (p) => Date.parse(p.from) <= at && (p.to === null || at < Date.parse(p.to)),
  );
  if (current) return { cents: current.cents, from: null };
  const next = mine
    .filter((p) => Date.parse(p.from) > at)
    .sort((a, b) => Date.parse(a.from) - Date.parse(b.from))[0];
  return next ? { cents: next.cents, from: next.from } : null;
}

export interface NewOffering {
  code: string; // upper case, validated
  name: string;
  specificationId: string;
  amount: string; // canonical decimal string, e.g. "1290.50"
  currency: string;
  validFrom: string; // business date: the price and the offering start at its midnight
  validTo: string | null; // business date, inclusive (last valid day); null = open-ended
}

/** Midnight of a business date in BILLING_TZ, as a TMF date-time. */
export function midnightOf(day: string, timeZone: string): string {
  return zonedDateTime(day, '00:00', timeZone);
}

/**
 * The design's single "create" is two TMF620 calls, as the spec models it: the price
 * first, then the offering that references it (prices attach through the offering). If
 * the second call fails, the price stays unattached - it can never price anything - and
 * the error is shown; nothing is rolled back behind the operator's back.
 */
export async function createOffering(form: NewOffering, timeZone: string): Promise<string> {
  const validFor = {
    startDateTime: midnightOf(form.validFrom, timeZone),
    // the UI's "Valid to" is the last valid day; TMF validFor ends exclusively
    ...(form.validTo === null
      ? {}
      : { endDateTime: midnightOf(addDays(form.validTo, 1), timeZone) }),
  };
  const price = await postJson<PriceBody>(
    PATHS.prices,
    jsonWithAmount(
      {
        '@type': 'ProductOfferingPrice',
        name: `${form.name} monthly`,
        lifecycleStatus: 'Active',
        priceType: 'recurring',
        recurringChargePeriodType: 'monthly',
        price: { unit: form.currency, value: AMOUNT },
        validFor,
      },
      form.amount,
    ),
  );
  const offering = await postJson<OfferingBody>(PATHS.offerings, {
    '@type': 'ProductOffering',
    name: form.name,
    lifecycleStatus: 'In Design', // every new offering starts in In Design
    validFor,
    productSpecification: { id: form.specificationId, '@type': 'ProductSpecificationRef' },
    productOfferingPrice: [{ id: need(price.id, 'price id'), '@type': 'ProductOfferingPriceRef' }],
    externalIdentifier: [
      { id: form.code, externalIdentifierType: 'offeringCode', '@type': 'ExternalIdentifier' },
    ],
  });
  return need(offering.id, 'offering id');
}

/**
 * Change an offering's recurring price from `validFrom` (a business date, from tomorrow):
 * one internal operation that ends the open price at that midnight and attaches the new
 * one - atomic, because TMF620's two steps cannot work while a product is live (D-W21).
 */
export async function changePrice(
  offeringId: string,
  amount: string,
  currency: string,
  validFrom: string,
): Promise<void> {
  await postJson(
    `/internal/v1/productOffering/${offeringId}/priceChange`,
    jsonWithAmount({ amount: AMOUNT, currency, validFrom }, amount),
  );
}

/** Whether the offering has an open-ended recurring price in `currency` - what a price
 * change replaces. */
export function hasOpenPrice(offering: CatalogOffering, currency: string): boolean {
  return offering.prices.some((p) => p.currency === currency && p.to === null);
}

/** The only lifecycle steps the UI offers: In Design -> Active -> Retired. */
export const NEXT_LIFECYCLE: Record<Lifecycle, Lifecycle | null> = {
  'In Design': 'Active',
  Active: 'Retired',
  Retired: null,
};

export async function setLifecycle(offeringId: string, status: Lifecycle): Promise<void> {
  await patchJson(`${PATHS.offerings}/${offeringId}`, { lifecycleStatus: status });
}

// --- orders (TMF622): the only way to add or end a product ---

type OrderBody = T622['schemas']['ProductOrder'];

export interface OrderOutcome {
  state: string; // completed | rejected
  reasons: string[]; // productOrderErrorMessage[].reason when rejected
}

function outcome(order: OrderBody): OrderOutcome {
  return {
    state: need(order.state, 'order state'),
    reasons: (order.productOrderErrorMessage ?? []).map((m) => m.reason ?? m.code ?? 'rejected'),
  };
}

/** The requested date as noon of that day in BILLING_TZ, with BILLING_TZ's offset on that
 * date: the backend takes the calendar date in BILLING_TZ. Never the browser's zone. */
export function requestedDate(day: string, timeZone: string): string {
  return zonedDateTime(day, '12:00', timeZone);
}

export async function orderAdd(
  accountId: string,
  offeringId: string,
  startDate: string,
  timeZone: string,
): Promise<OrderOutcome> {
  const order = await postJson<OrderBody>(PATHS.orders, {
    '@type': 'ProductOrder',
    billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
    requestedStartDate: requestedDate(startDate, timeZone),
    productOrderItem: [
      {
        '@type': 'ProductOrderItem',
        id: '1',
        action: 'add',
        productOffering: { id: offeringId, '@type': 'ProductOfferingRef' },
      },
    ],
  });
  return outcome(order);
}

/** Deactivate = a delete item, effective today (no requestedStartDate). A started
 * product ends today (not billed from today); one not started yet is cancelled. */
export async function orderDelete(accountId: string, productId: string): Promise<OrderOutcome> {
  const order = await postJson<OrderBody>(PATHS.orders, {
    '@type': 'ProductOrder',
    billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
    productOrderItem: [
      {
        '@type': 'ProductOrderItem',
        id: '1',
        action: 'delete',
        product: { id: productId, '@type': 'ProductRef' },
      },
    ],
  });
  return outcome(order);
}

// --- billing runs (stage 4): the worker bills; the UI reads runs and queues requests ---

export type BillingRun = Internal['schemas']['BillingRun'];
export type RunAccount = Internal['schemas']['RunAccount'];
export type RunOutcome = RunAccount['status'];

/** How many runs the screen shows: the latest and its history (stage4-design section 7). */
export const RUN_HISTORY = 14;

/** The newest runs first; `total` is every run there is. */
export function loadRuns(signal: AbortSignal, limit = RUN_HISTORY): Promise<Page<BillingRun>> {
  return getPage<BillingRun>(`${PATHS.runs}?limit=${limit}`, signal);
}

export function loadRun(id: string, signal: AbortSignal): Promise<BillingRun> {
  return getJson<BillingRun>(`${PATHS.runs}/${id}`, signal);
}

/** One page (100) of a run's per-account outcomes, optionally of one status. */
export function loadRunAccounts(
  id: string,
  status: RunOutcome | null,
  signal: AbortSignal,
): Promise<Page<RunAccount>> {
  const query = status === null ? 'limit=100' : `status=${status}&limit=100`;
  return getPage<RunAccount>(`${PATHS.runs}/${id}/accounts?${query}`, signal);
}

export interface Queued {
  dates: string[]; // what a catch-up will run, in order (empty for a retry)
  requestedAt: string; // the server's clock: runs started from then on carry it out
}

/** Queue a catch-up to `upTo` (a business date). The API never bills: the worker runs
 * the dates in order within a minute. null = nothing was due, nothing was queued. */
export async function catchUp(upTo: string): Promise<Queued | null> {
  const result = await postJson<Internal['schemas']['CatchUp']>(`${PATHS.runs}/catchUp`, {
    upTo,
  });
  if (result.dates.length === 0) return null;
  return { dates: result.dates, requestedAt: need(result.request, 'request').requestedAt };
}

/** Queue a retry of a failed run (any failed run, Q3). The endpoint takes no body. */
export async function retryRun(id: string): Promise<Queued> {
  const response = await request('POST', `${PATHS.runs}/${id}/retry`);
  const result = (await response.json()) as Internal['schemas']['RetryQueued'];
  return { dates: [], requestedAt: result.request.requestedAt };
}
