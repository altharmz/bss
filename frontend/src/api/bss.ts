// What the screens load and send, mapped from the generated spec types onto exactly what
// the UI needs. The TMF types make every attribute optional; our API always sends the
// ones read here, so a missing one is an error (shown in the error block), never a
// silently blank cell.

import { toCents } from '../format/money';
import { addDays, businessDate, zonedDateTime } from '../format/dates';
import {
  AMOUNT,
  amountSlot,
  explain,
  getAll,
  getJson,
  getPage,
  jsonWithAmount,
  jsonWithAmounts,
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
import type { components as T678 } from './generated/tmf678';

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

// --- subscribers: create and edit (TMF632 party, TMF629 customer, TMF666 account) ---
// A subscriber is three resources. There is no delete (CLAUDE.md, "Frontend (stage 5)
// decisions"): an account carries products, bills and payments; it can be Closed.

const PARTIES = '/tmf-api/partyManagement/v5';

export type PartyKind = 'Individual' | 'Organization';
export type CustomerStatus = 'Created' | 'Active' | 'Inactive';

export interface NewSubscriber {
  kind: PartyKind;
  givenName: string; // Individual
  familyName: string; // Individual
  organizationName: string; // Organization
  accountName: string;
  cycleDay: number; // 1..28
  currency: string;
  creditLimit: string | null; // canonical decimal string, or none
}

export function partyName(form: {
  kind: PartyKind;
  givenName: string;
  familyName: string;
  organizationName: string;
}): string {
  return form.kind === 'Individual'
    ? `${form.givenName.trim()} ${form.familyName.trim()}`
    : form.organizationName.trim();
}

/** Which of the three creates failed, and what already exists then. */
export class SubscriberStepError extends Error {}

function partyBody(form: NewSubscriber | SubscriberProfile): Record<string, string> {
  return form.kind === 'Individual'
    ? { givenName: form.givenName.trim(), familyName: form.familyName.trim() }
    : { name: form.organizationName.trim() };
}

function partyPath(kind: PartyKind): string {
  return `${PARTIES}/${kind === 'Individual' ? 'individual' : 'organization'}`;
}

/**
 * Three TMF calls, in the order the references require: the party, the customer engaging
 * it (Active: the operator registers a real customer), then its billing account. A failure
 * stops there and says what was already created - nothing is deleted behind the
 * operator's back (there is no DELETE), as with New offering. Returns the account id.
 */
export async function createSubscriber(form: NewSubscriber): Promise<string> {
  const name = partyName(form);
  const created: string[] = [];
  async function step<T>(what: string, call: () => Promise<T>): Promise<T> {
    try {
      return await call();
    } catch (error) {
      const done = created.length ? ` Already created: ${created.join(', ')}.` : '';
      throw new SubscriberStepError(`Creating the ${what} failed: ${explain(error)}.${done}`);
    }
  }
  const party = await step('party', () =>
    postJson<{ id?: string }>(partyPath(form.kind), { '@type': form.kind, ...partyBody(form) }),
  );
  const partyId = need(party.id, 'party id');
  created.push(`${form.kind.toLowerCase()} ${name}`);
  const customer = await step('customer', () =>
    postJson<{ id?: string }>(PATHS.customers, {
      '@type': 'Customer',
      name,
      status: 'Active',
      engagedParty: { id: partyId, '@type': 'PartyRef', '@referredType': form.kind },
    }),
  );
  const customerId = need(customer.id, 'customer id');
  created.push(`customer ${name}`);
  const body = {
    '@type': 'BssBillingAccount',
    name: form.accountName.trim(),
    billCycleDay: form.cycleDay,
    currency: form.currency,
    relatedParty: [
      {
        '@type': 'RelatedPartyRefOrPartyRoleRef',
        role: 'customer',
        partyOrPartyRole: {
          id: customerId,
          name,
          '@type': 'PartyRoleRef',
          '@referredType': 'Customer',
        },
      },
    ],
    ...(form.creditLimit === null ? {} : { creditLimit: { unit: form.currency, value: AMOUNT } }),
  };
  const account = await step('billing account', () =>
    postJson<{ id?: string }>(
      PATHS.accounts,
      form.creditLimit === null ? body : jsonWithAmount(body, form.creditLimit),
    ),
  );
  return need(account.id, 'account id');
}

/** Everything the Edit dialog shows and may change. */
export interface SubscriberProfile {
  kind: PartyKind;
  partyId: string;
  givenName: string;
  familyName: string;
  organizationName: string;
  customerId: string;
  customerName: string;
  customerStatus: CustomerStatus;
  accountId: string;
  accountName: string;
  accountState: AccountState;
  creditLimit: string | null; // canonical decimal string, or none
}

function canonical(cents: number | null): string | null {
  if (cents === null) return null;
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`;
}

export async function loadProfile(
  account: Account,
  signal: AbortSignal,
): Promise<SubscriberProfile> {
  const customer = await getJson<T629['schemas']['Customer']>(
    `${PATHS.customers}/${account.customerId}`,
    signal,
  );
  const engaged = need(customer.engagedParty, 'customer engagedParty');
  const kind = need(engaged['@referredType'], 'engagedParty @referredType') as PartyKind;
  const partyId = need(engaged.id, 'engagedParty id');
  const party = await getJson<{ givenName?: string; familyName?: string; name?: string }>(
    `${partyPath(kind)}/${partyId}`,
    signal,
  );
  return {
    kind,
    partyId,
    givenName: party.givenName ?? '',
    familyName: party.familyName ?? '',
    organizationName: kind === 'Organization' ? need(party.name, 'organization name') : '',
    customerId: account.customerId,
    customerName: need(customer.name, 'customer name'),
    customerStatus: need(customer.status, 'customer status') as CustomerStatus,
    accountId: account.id,
    accountName: account.name,
    accountState: account.state,
    creditLimit: canonical(account.creditLimitCents),
  };
}

/**
 * Merge patches of only what changed, party then customer then account. Returns what was
 * changed ("party", "customer", "account"); a failure says what was already saved.
 */
export async function updateSubscriber(
  before: SubscriberProfile,
  after: SubscriberProfile,
  currency: string,
): Promise<string[]> {
  const saved: string[] = [];
  async function step(what: string, call: () => Promise<unknown>): Promise<void> {
    try {
      await call();
      saved.push(what);
    } catch (error) {
      const done = saved.length ? ` Already saved: ${saved.join(', ')}.` : '';
      throw new SubscriberStepError(`Saving the ${what} failed: ${explain(error)}.${done}`);
    }
  }
  const party = Object.fromEntries(
    Object.entries(partyBody(after)).filter(([k, v]) => partyBody(before)[k] !== v),
  );
  if (Object.keys(party).length) {
    await step('party', () => patchJson(`${partyPath(after.kind)}/${after.partyId}`, party));
  }
  const customer: Record<string, string> = {};
  if (after.customerName.trim() !== before.customerName) customer.name = after.customerName.trim();
  if (after.customerStatus !== before.customerStatus) customer.status = after.customerStatus;
  if (Object.keys(customer).length) {
    await step('customer', () => patchJson(`${PATHS.customers}/${after.customerId}`, customer));
  }
  const account: Record<string, unknown> = {};
  if (after.accountName.trim() !== before.accountName) account.name = after.accountName.trim();
  if (after.accountState !== before.accountState) account.state = after.accountState;
  const limitChanged = after.creditLimit !== before.creditLimit;
  if (limitChanged) {
    account.creditLimit = after.creditLimit === null ? null : { unit: currency, value: AMOUNT };
  }
  if (Object.keys(account).length) {
    await step('billing account', () =>
      patchJson(
        `${PATHS.accounts}/${after.accountId}`,
        limitChanged && after.creditLimit !== null
          ? jsonWithAmount(account, after.creditLimit)
          : account,
      ),
    );
  }
  return saved;
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

// --- bills and payments (bill lifecycle, docs/bill-lifecycle-design.md section 6) ---

export const BILLS = '/tmf-api/customerBillManagement/v5/customerBill';
export const PAYMENTS = '/tmf-api/paymentManagement/v4/payment';

export type BillState = T678['schemas']['CustomerBillStateType'];
export type AccountSettlement = Internal['schemas']['AccountSettlement'];
export type BillDetail = Internal['schemas']['BillDetail'];

export interface Bill {
  id: string;
  billNo: string;
  billDate: string; // business date
  periodEnd: string; // business date, exclusive
  dueDate: string; // business date
  amountDueCents: number;
  remainingCents: number;
  state: BillState;
  creditNote: boolean;
}

export function toBill(body: T678['schemas']['CustomerBill'], timeZone: string): Bill {
  return {
    id: need(body.id, 'id'),
    billNo: need(body.billNo, 'billNo'),
    billDate: businessDate(need(body.billDate, 'billDate'), timeZone),
    periodEnd: businessDate(need(body.billingPeriod?.endDateTime, 'billingPeriod'), timeZone),
    dueDate: businessDate(need(body.paymentDueDate, 'paymentDueDate'), timeZone),
    amountDueCents: toCents(need(body.amountDue?.value, 'amountDue')),
    remainingCents: toCents(need(body.remainingAmount?.value, 'remainingAmount')),
    state: need(body.state, 'state'),
    creditNote: body.category === 'creditNote',
  };
}

/** The account's bills, newest first. */
export async function loadBills(
  accountId: string,
  timeZone: string,
  signal: AbortSignal,
): Promise<Bill[]> {
  const bodies = await getAll<T678['schemas']['CustomerBill']>(
    `${BILLS}?billingAccount.id=${accountId}`,
    signal,
  );
  return bodies
    .map((b) => toBill(b, timeZone))
    .sort((a, b) => b.billDate.localeCompare(a.billDate));
}

export function loadSettlement(accountId: string, signal: AbortSignal): Promise<AccountSettlement> {
  return getJson<AccountSettlement>(`${PATHS.internal}/${accountId}/settlement`, signal);
}

export function loadBillDetail(billId: string, signal: AbortSignal): Promise<BillDetail> {
  return getJson<BillDetail>(`/internal/v1/customerBill/${billId}`, signal);
}

/** Hold or release a bill: TMF678 PATCH of its state only. The backend decides whether
 * the change is allowed (the dialog offers only BillDetail.allowedStates). */
export async function setBillState(billId: string, state: BillState): Promise<void> {
  await patchJson(`${BILLS}/${billId}`, { state });
}

export interface PaymentForm {
  amount: string; // canonical decimal string
  currency: string;
  method: string; // stored as the payment method's name
  items: { billId: string; amount: string }[]; // explicit lettering; empty = oldest first
}

/** TMF676 POST /payment with exact amount tokens. Returns how many bills it paid now. */
export async function recordPayment(accountId: string, form: PaymentForm): Promise<number> {
  const body = {
    '@type': 'Payment',
    account: { id: accountId, '@referredType': 'BillingAccount' },
    totalAmount: { unit: form.currency, value: amountSlot(0) },
    paymentMethod: { name: form.method },
    ...(form.items.length === 0
      ? {}
      : {
          paymentItem: form.items.map((item, i) => ({
            '@type': 'PaymentItem',
            item: { id: item.billId, '@referredType': 'CustomerBill' },
            amount: { unit: form.currency, value: amountSlot(i + 1) },
          })),
        }),
  };
  const created = await postJson<{ paymentItem?: unknown[] }>(
    PAYMENTS,
    jsonWithAmounts(body, [form.amount, ...form.items.map((i) => i.amount)]),
  );
  return created.paymentItem?.length ?? 0;
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
