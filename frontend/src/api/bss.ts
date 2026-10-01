// What the screens load and send, mapped from the generated spec types onto exactly what
// the UI needs. The TMF types make every attribute optional; our API always sends the
// ones read here, so a missing one is an error (shown in the error block), never a
// silently blank cell.

import { toCents } from '../format/money';
import { getAll, getJson, postJson } from './client';
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

/** The requested date as noon of that day in BILLING_TZ: the backend takes the calendar
 * date in BILLING_TZ, and noon keeps it on the right day whatever the offset is. */
export function requestedDate(day: string, utcOffset: string): string {
  return `${day}T12:00:00${utcOffset}`;
}

export async function orderAdd(
  accountId: string,
  offeringId: string,
  startDate: string,
  utcOffset: string,
): Promise<OrderOutcome> {
  const order = await postJson<OrderBody>(PATHS.orders, {
    '@type': 'ProductOrder',
    billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
    requestedStartDate: requestedDate(startDate, utcOffset),
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
