// What the screens load and send, mapped from the generated spec types onto exactly what
// the UI needs. The TMF types make every attribute optional; our API always sends the
// ones read here, so a missing one is an error (shown in the error block), never a
// silently blank cell.

import { toCents } from '../format/money';
import { numberProduct } from './numbers';
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
  jsonWithSignedAmount,
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
  financialAccounts: '/tmf-api/accountManagement/v5/financialAccount',
  customers: '/tmf-api/customer/v5/customer',
  products: '/tmf-api/productInventory/v5/product',
  offerings: '/tmf-api/productCatalogManagement/v5/productOffering',
  prices: '/tmf-api/productCatalogManagement/v5/productOfferingPrice',
  orders: '/tmf-api/productOrderingManagement/v5/productOrder',
  cancellations: '/tmf-api/productOrderingManagement/v5/cancelProductOrder',
  internal: '/internal/v1/billingAccount',
  runs: '/internal/v1/billingRun',
  buckets: '/tmf-api/prepayBalanceManagement/v4/bucket',
  topups: '/tmf-api/prepayBalanceManagement/v4/topupBalance',
  adjustments: '/tmf-api/prepayBalanceManagement/v4/adjustBalance',
  reservations: '/tmf-api/prepayBalanceManagement/v4/reserveBalance',
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
  ratingType?: string;
};

/** The account's rating mode (docs/decisions/prepaid.md); chosen at creation. */
export type RatingMode = 'postpaid' | 'prepaid';

export type AccountState = 'Active' | 'Suspended' | 'Closed';

export interface Account {
  id: string;
  name: string;
  state: AccountState;
  ratingType: RatingMode;
  customerId: string;
  customerName: string;
  cycleDay: number;
  currency: string;
  balanceCents: number; // positive = in credit
  creditLimitCents: number | null;
  financialAccount: { id: string; name: string } | null;
}

export function toAccount(body: BillingAccountBody): Account {
  // relatedParty may hold other roles too (bill receiver, contact): find the customer
  const customer = need(
    body.relatedParty?.find((party) => party.role === 'customer')?.partyOrPartyRole,
    'relatedParty customer',
  );
  const balance = need(body.accountBalance?.[0]?.amount?.value, 'accountBalance');
  const limit = body.creditLimit?.value;
  const financial = body.financialAccount;
  return {
    financialAccount: financial
      ? { id: need(financial.id, 'financialAccount id'), name: financial.name ?? '' }
      : null,
    id: need(body.id, 'id'),
    name: need(body.name, 'name'),
    state: need(body.state, 'state') as AccountState,
    ratingType: need(body.ratingType, 'ratingType') as RatingMode,
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

// --- prepaid balance (TMF654 v4 bucket, docs/decisions/prepaid.md#p4c) ---

/** A prepaid account's funds, kept distinct: the ledger is what the account holds,
 * reserved the outstanding holds, available (spendable) = ledger - reserved, unclamped. */
export interface PrepaidBalance {
  bucketId: string;
  ledgerCents: number;
  reservedCents: number;
  availableCents: number;
}

interface BucketBody {
  id?: string;
  remainingValue?: { amount?: number; units?: string };
  reservedValue?: { amount?: number; units?: string };
}

export async function loadPrepaidBalance(
  accountId: string,
  signal: AbortSignal,
): Promise<PrepaidBalance> {
  const buckets = await getAll<BucketBody>(`${PATHS.buckets}?partyAccount.id=${accountId}`, signal);
  const bucket = need(buckets[0], 'prepaid bucket');
  const available = toCents(need(bucket.remainingValue?.amount, 'remainingValue'));
  const reserved = toCents(need(bucket.reservedValue?.amount, 'reservedValue'));
  return {
    bucketId: need(bucket.id, 'bucket id'),
    availableCents: available,
    reservedCents: reserved,
    ledgerCents: available + reserved,
  };
}

// --- prepaid money forms (TMF654 topupBalance / adjustBalance / reserveBalance, P4d-P4f) ---

export type BalanceActionKind = 'topup' | 'credit' | 'debit' | 'hold';

export interface BalanceActionForm {
  kind: BalanceActionKind;
  accountId: string;
  bucketId: string;
  currency: string;
  amount: string; // canonical decimal string, positive; a debit is sent negative
  method: string; // top-up only: the payment method name
  reason: string;
  expiresOn: string | null; // hold only: last business day the hold lasts through
}

/** The exact request the form sends: path and JSON text (the amount as a verbatim token). */
export function balanceActionRequest(form: BalanceActionForm, timeZone: string) {
  const quantity = { amount: AMOUNT, units: form.currency };
  const bucket = { id: form.bucketId };
  const owner = { id: form.accountId, '@referredType': 'BillingAccount' };
  const reason = form.reason.trim() ? { reason: form.reason.trim() } : {};
  switch (form.kind) {
    case 'topup':
      return {
        path: PATHS.topups,
        body: jsonWithAmount(
          {
            '@type': 'TopupBalance',
            amount: quantity,
            usageType: 'monetary',
            bucket,
            partyAccount: owner,
            paymentMethod: { id: form.method, name: form.method },
            ...reason,
          },
          form.amount,
        ),
      };
    case 'credit':
    case 'debit':
      return {
        path: PATHS.adjustments,
        body: jsonWithSignedAmount(
          { '@type': 'AdjustBalance', amount: quantity, usageType: 'monetary', bucket, ...reason },
          form.kind === 'debit' ? `-${form.amount}` : form.amount,
        ),
      };
    case 'hold':
      return {
        path: PATHS.reservations,
        body: jsonWithAmount(
          {
            '@type': 'ReserveBalance',
            amount: quantity,
            usageType: 'monetary',
            bucket,
            partyAccount: owner,
            ...reason,
            ...(form.expiresOn === null
              ? {}
              : { validFor: { endDateTime: midnightOf(addDays(form.expiresOn, 1), timeZone) } }),
          },
          form.amount,
        ),
      };
  }
}

/** What the API stored: a business refusal (not enough spendable funds, a closed account)
 * is a 201 whose status is `failed` and whose `reason` says why. */
export interface BalanceActionResult {
  status: string;
  reason: string | null;
}

export async function submitBalanceAction(
  request: { path: string; body: string },
  key: string,
): Promise<BalanceActionResult> {
  const stored = await postJson<{ status?: string; reason?: string }>(request.path, request.body, {
    'Idempotency-Key': key,
  });
  return { status: need(stored.status, 'status'), reason: stored.reason ?? null };
}

// --- financial accounts (TMF666): read-only in the UI (docs/decisions/accounts.md) ---

export type ContainedKind = 'BillingAccount' | 'PartyAccount' | 'SettlementAccount';

export interface FinancialAccount {
  id: string;
  name: string;
  state: AccountState;
  accountType: string | null;
  ownerName: string;
  ownerKind: string; // Individual | Organization
  balances: { currency: string; cents: number }[]; // positive = in credit
  contains: { id: string; name: string; kind: ContainedKind }[];
}

export function toFinancialAccount(body: T666['schemas']['FinancialAccount']): FinancialAccount {
  const owner = need(
    body.relatedParty?.find((party) => party.role === 'owner')?.partyOrPartyRole,
    'relatedParty owner',
  );
  return {
    id: need(body.id, 'id'),
    name: need(body.name, 'name'),
    state: need(body.state, 'state') as AccountState,
    accountType: body.accountType ?? null,
    ownerName: need(owner.name, 'owner name'),
    ownerKind: need(owner['@referredType'], 'owner @referredType'),
    balances: need(body.accountBalance, 'accountBalance').map((balance) => ({
      currency: need(balance.amount?.unit, 'balance unit'),
      cents: toCents(need(balance.amount?.value, 'balance value')),
    })),
    contains: need(body.accountRelationship, 'accountRelationship').map((relation) => {
      const account = need(relation.account, 'accountRelationship account');
      return {
        id: need(account.id, 'contained account id'),
        name: account.name ?? '',
        kind: need(account['@referredType'], 'contained @referredType') as ContainedKind,
      };
    }),
  };
}

export async function loadFinancialAccounts(signal: AbortSignal): Promise<FinancialAccount[]> {
  type Body = T666['schemas']['FinancialAccount'];
  return (await getAll<Body>(PATHS.financialAccounts, signal)).map(toFinancialAccount);
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
// A subscriber is three resources. There is no delete (docs/decisions/frontend.md):
// an account carries products, bills and payments; it can be Closed.

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
  creditLimit: string | null; // canonical decimal string, or none (always none when prepaid)
  ratingType: RatingMode;
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
    // sent only for prepaid: a postpaid body stays what it always was (the default)
    ...(form.ratingType === 'prepaid' ? { ratingType: 'prepaid' } : {}),
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
  specificationId?: string | undefined;
  name: string;
  code: string | null;
  specification: string;
  priceCents: number | null; // display only; the charge itself comes from the preview
  interval?: string;
}

export function chargeInterval(type = 'monthly', length = 1): string {
  const unit: Record<string, string> = {
    daily: 'day',
    weekly: 'week',
    monthly: 'month',
    yearly: 'year',
  };
  const name = unit[type] ?? type;
  return length === 1 ? name : `${length} ${name}s`;
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
      return {
        cents: toCents(value),
        interval: chargeInterval(
          price.recurringChargePeriodType,
          price.recurringChargePeriodLength,
        ),
      };
    }
  }
  return null;
}

/** The offering code: the externalIdentifier entry of type offeringCode. */
export function offeringCode(offering: Pick<OfferingBody, 'externalIdentifier'>): string | null {
  const entry = (offering.externalIdentifier ?? []).find(
    (e) => e.externalIdentifierType === 'offeringCode',
  );
  return entry?.id ?? null;
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
    code: offeringCode(offering),
    specification: offering.productSpecification?.name ?? '',
    specificationId: offering.productSpecification?.id,
    priceCents: displayPrice(offering, byId, now, currency)?.cents ?? null,
    interval: displayPrice(offering, byId, now, currency)?.interval ?? 'month',
  }));
}

// --- the catalog (TMF620) ---

export type Lifecycle = 'In Design' | 'Active' | 'Retired';
type SpecBody = T620['schemas']['ProductSpecification'];

export interface CatalogPrice {
  id: string;
  cents: number;
  currency: string;
  interval?: string;
  from: string; // instant
  to: string | null; // instant, exclusive; null = open-ended
}

export interface CatalogOffering {
  id: string;
  name: string;
  code: string | null;
  status: Lifecycle;
  specification: string;
  validFrom: string | null; // instant; null = no validFor (valid at any time)
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
      code: offeringCode(offering),
      status: need(offering.lifecycleStatus, 'lifecycleStatus') as Lifecycle,
      specification: offering.productSpecification?.name ?? '',
      validFrom: offering.validFor?.startDateTime ?? null,
      validTo: offering.validFor?.endDateTime ?? null,
      prices: (offering.productOfferingPrice ?? []).flatMap((ref) => {
        const price = ref.id === undefined ? undefined : priceById.get(ref.id);
        if (price === undefined) return []; // not recurring (or not visible): not listed
        return [
          {
            id: need(price.id, 'price id'),
            cents: toCents(need(price.price?.value, 'price value')),
            currency: need(price.price?.unit, 'price unit'),
            interval: chargeInterval(
              price.recurringChargePeriodType,
              price.recurringChargePeriodLength,
            ),
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
): { cents: number; from: string | null; interval?: string } | null {
  const at = Date.parse(now);
  const mine = prices.filter((p) => p.currency === currency);
  const current = mine.find(
    (p) => Date.parse(p.from) <= at && (p.to === null || at < Date.parse(p.to)),
  );
  if (current) return { cents: current.cents, from: null, interval: current.interval ?? 'month' };
  const next = mine
    .filter((p) => Date.parse(p.from) > at)
    .sort((a, b) => Date.parse(a.from) - Date.parse(b.from))[0];
  return next ? { cents: next.cents, from: next.from, interval: next.interval ?? 'month' } : null;
}

export interface NewOffering {
  code: string; // upper case, validated
  name: string;
  specificationId: string;
  amount: string; // canonical decimal string, e.g. "1290.50"
  currency: string;
  recurringPeriodType?: string;
  recurringPeriodLength?: number;
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
        name: `${form.name} ${form.recurringPeriodType ?? 'monthly'}`,
        lifecycleStatus: 'Active',
        priceType: 'recurring',
        recurringChargePeriodType: form.recurringPeriodType ?? 'monthly',
        ...((form.recurringPeriodLength ?? 1) !== 1 ||
        (form.recurringPeriodType ?? 'monthly') !== 'monthly'
          ? { recurringChargePeriodLength: form.recurringPeriodLength ?? 1 }
          : {}),
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
  id: string;
  state: string; // completed | inProgress (prepaid: the worker finishes it) | rejected
  reasons: string[]; // productOrderErrorMessage[].reason when rejected
}

function outcome(order: OrderBody): OrderOutcome {
  return {
    id: order.id ?? '', // always sent; only an inProgress order is followed by it
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
  numberId?: string,
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
        ...(numberId ? { product: numberProduct(numberId) } : {}),
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

type ProductBody = T637['schemas']['Product'];

/** Undo a pending deactivation: cancel (TMF622 cancelProductOrder) the order whose delete
 * item will end the product. The backend refuses (409) once the termination happened or
 * billing charged its period; the product is open-ended again otherwise. */
export async function undoDeactivation(productId: string): Promise<void> {
  const product = await getJson<ProductBody>(`${PATHS.products}/${productId}`);
  const ending = (product.productOrderItem ?? []).find((item) => item.role === 'termination');
  await postJson(PATHS.cancellations, {
    '@type': 'CancelProductOrder',
    productOrder: {
      id: need(ending?.orderId, 'terminating order'),
      '@type': 'ProductOrderRef',
    },
  });
}

// --- bills and payments (bill lifecycle, docs/archive/bill-lifecycle-design.md section 6) ---

export const BILLS = '/tmf-api/customerBillManagement/v5/customerBill';
export const PAYMENTS = '/tmf-api/payment/v4/payment';

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

// --- recorded payouts and asynchronous on-demand bills ---

export const REFUNDS = '/tmf-api/payment/v4/refund';
export const BILL_DEMANDS = '/tmf-api/customerBillManagement/v5/customerBillOnDemand';

export interface RecordedRefund {
  id: string;
  refundDate: string;
  totalAmount: { value: number; unit: string };
  paymentMethod: { name: string };
  description?: string;
}

export type BillDemand = T678['schemas']['CustomerBillOnDemand'];

export function loadRefunds(accountId: string, signal?: AbortSignal) {
  return getAll<RecordedRefund>(`${REFUNDS}?account.id=${encodeURIComponent(accountId)}`, signal);
}

export function loadBillDemands(accountId: string, signal?: AbortSignal) {
  return getAll<BillDemand>(
    `${BILL_DEMANDS}?billingAccount.id=${encodeURIComponent(accountId)}`,
    signal,
  );
}

export function recordRefund(
  account: Account,
  amount: string,
  method: string,
  description: string,
) {
  return postJson<RecordedRefund>(
    REFUNDS,
    jsonWithAmount(
      {
        '@type': 'Refund',
        account: { id: account.id, '@referredType': 'BillingAccount' },
        totalAmount: { unit: account.currency, value: AMOUNT },
        paymentMethod: { name: method },
        ...(description.trim() ? { description: description.trim() } : {}),
      },
      amount,
    ),
  );
}

export function requestBill(accountId: string) {
  return postJson<BillDemand>(BILL_DEMANDS, {
    '@type': 'CustomerBillOnDemand',
    billingAccount: { id: accountId, '@referredType': 'BillingAccount' },
  });
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
