import { orderRefusal, type OrderError } from './orderErrors';
// What the screens load and send, mapped from the generated spec types onto exactly what
// the UI needs. The TMF types make every attribute optional; our API always sends the
// ones read here, so a missing one is an error (shown in the error block), never a
// silently blank cell.

import { toCents } from '../format/money';
import { numberProduct } from './numbers';
import type { SimType } from './sims';
import { contactMedia, EMPTY_CONTACT } from './partyDocuments';
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
  partyAccounts: '/tmf-api/accountManagement/v5/partyAccount',
  settlementAccounts: '/tmf-api/accountManagement/v5/settlementAccount',
  customers: '/tmf-api/customer/v5/customer',
  products: '/tmf-api/productInventory/v5/product',
  offerings: '/tmf-api/productCatalogManagement/v5/productOffering',
  specifications: '/tmf-api/productCatalogManagement/v5/productSpecification',
  prices: '/tmf-api/productCatalogManagement/v5/productOfferingPrice',
  categories: '/tmf-api/productCatalogManagement/v5/category',
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
export type PendingChange = AccountProduct['pendingChanges'][number];
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
  /** the bill structure the account prefers (slice 18); absent = the defaults */
  billing?: BillingPrefs | undefined;
}

/** The ids of the cycle specification (null = the built-in monthly), bill format and
 * presentation media a billing account names. */
export interface BillingPrefs {
  cycleId: string | null;
  formatId: string | null;
  mediaIds: string[];
}

export const BUILT_IN_CYCLE = 'monthly';

function toBilling(structure: T666['schemas']['BillingAccount']['billStructure']): BillingPrefs {
  const cycle = structure?.cycleSpecification?.id;
  return {
    cycleId: cycle === undefined || cycle === BUILT_IN_CYCLE ? null : cycle,
    formatId: structure?.format?.id ?? null,
    mediaIds: (structure?.presentationMedia ?? []).flatMap((m) => (m.id ? [m.id] : [])),
  };
}

const BILLING_REF = {
  cycle: { '@type': 'BillingCycleSpecificationRef', '@referredType': 'BillingCycleSpecification' },
  format: { '@type': 'BillFormatRef', '@referredType': 'BillFormat' },
  media: { '@type': 'BillPresentationMediaRef', '@referredType': 'BillPresentationMedia' },
} as const;

/** The billStructure of the billing account merge patch: only the parts that changed. The
 * media list is replaced as a whole (as the API defines it); the built-in cycle is sent as
 * the id `monthly`. null = nothing changed. */
export function billStructurePatch(
  before: BillingPrefs,
  after: BillingPrefs,
): Record<string, unknown> | null {
  const body: Record<string, unknown> = {};
  if (after.cycleId !== before.cycleId) {
    body.cycleSpecification = { id: after.cycleId ?? BUILT_IN_CYCLE, ...BILLING_REF.cycle };
  }
  if (after.formatId !== before.formatId) {
    body.format = after.formatId === null ? null : { id: after.formatId, ...BILLING_REF.format };
  }
  if (JSON.stringify(after.mediaIds) !== JSON.stringify(before.mediaIds)) {
    body.presentationMedia = after.mediaIds.map((id) => ({ id, ...BILLING_REF.media }));
  }
  return Object.keys(body).length === 0 ? null : body;
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
    billing: toBilling(body.billStructure),
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
  numberId?: string; // top-up only: optional active logical resource
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
            ...(form.numberId ? { logicalResource: [{ id: form.numberId }] } : {}),
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

// parity: POST ${PATHS.topups}
// parity: POST ${PATHS.adjustments}
// parity: POST ${PATHS.reservations}
export async function submitBalanceAction(
  request: { path: string; body: string },
  key: string,
): Promise<BalanceActionResult> {
  const stored = await postJson<{ status?: string; reason?: string }>(request.path, request.body, {
    'Idempotency-Key': key,
  });
  return { status: need(stored.status, 'status'), reason: stored.reason ?? null };
}

// --- financial accounts (TMF666): list, create, edit, delete, link (docs/decisions/accounts.md) ---

export type ContainedKind = 'BillingAccount' | 'PartyAccount' | 'SettlementAccount';

/** What the financial, party and settlement accounts share. */
export interface AccountRecord {
  id: string;
  name: string;
  state: AccountState;
  accountType: string | null;
  ownerName: string;
  ownerKind: string; // Individual | Organization
  description: string | null;
  /** the informational credit limit: canonical decimal string and currency */
  creditLimit: { value: string; unit: string } | null;
  ownerId: string;
  /** relatedParty entries in other roles, sent back unchanged when the owner changes */
  otherParties: unknown[];
}

export interface FinancialAccount extends AccountRecord {
  balances: { currency: string; cents: number }[]; // positive = in credit
  contains: { id: string; name: string; kind: ContainedKind }[];
}

type AccountBody = Pick<
  T666['schemas']['FinancialAccount'],
  'id' | 'name' | 'state' | 'accountType' | 'description' | 'creditLimit' | 'relatedParty'
>;

/** What the financial, party and settlement account bodies share (the owner is its role). */
function accountCore(body: AccountBody) {
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
    ownerId: need(owner.id, 'owner id'),
    description: body.description ?? null,
    creditLimit:
      body.creditLimit?.value === undefined
        ? null
        : {
            value: canonical(toCents(body.creditLimit.value)) ?? '',
            unit: need(body.creditLimit.unit, 'creditLimit unit'),
          },
    otherParties: (body.relatedParty ?? []).filter((party) => party.role !== 'owner'),
  };
}

export function toFinancialAccount(body: T666['schemas']['FinancialAccount']): FinancialAccount {
  return {
    ...accountCore(body),
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

/** A party or settlement account: the same record, no balance, one optional financial account. */
export type PartyAccountKind = 'PartyAccount' | 'SettlementAccount';

export interface PartyAccount extends AccountRecord {
  kind: PartyAccountKind;
  financial: { id: string; name: string } | null;
}

export function toPartyAccount(
  body: T666['schemas']['PartyAccount'],
  kind: PartyAccountKind,
): PartyAccount {
  return {
    ...accountCore(body),
    kind,
    financial: body.financialAccount
      ? {
          id: need(body.financialAccount.id, 'financialAccount id'),
          name: body.financialAccount.name ?? '',
        }
      : null,
  };
}

export async function loadPartyAccounts(
  kind: PartyAccountKind,
  signal: AbortSignal,
): Promise<PartyAccount[]> {
  const path = kind === 'PartyAccount' ? PATHS.partyAccounts : PATHS.settlementAccounts;
  return (await getAll<T666['schemas']['PartyAccount']>(path, signal)).map((body) =>
    toPartyAccount(body, kind),
  );
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
// A subscriber is three resources. An account with history is not deleted
// (it carries products, bills and payments); it can be Closed.

const PARTIES = '/tmf-api/partyManagement/v5';

export type PartyKind = 'Individual' | 'Organization';
export type CustomerStatus = 'Created' | 'Active' | 'Inactive';

export interface NewSubscriber {
  kind: PartyKind;
  givenName: string; // Individual
  familyName: string; // Individual
  organizationName: string; // Organization
  email: string;
  phone: string;
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

/** A record a failed create could not remove again, for the error to name. */
export interface Leftover {
  what: string;
  id: string;
}

/** Which create failed, and which records are left behind then (none after a clean rollback). */
export class SubscriberStepError extends Error {
  constructor(
    message: string,
    readonly leftovers: Leftover[] = [],
  ) {
    super(message);
  }
}

function partyBody(form: NewSubscriber | SubscriberProfile): Record<string, string> {
  return form.kind === 'Individual'
    ? { givenName: form.givenName.trim(), familyName: form.familyName.trim() }
    : { name: form.organizationName.trim() };
}

function partyPath(kind: PartyKind): string {
  return `${PARTIES}/${kind === 'Individual' ? 'individual' : 'organization'}`;
}

/** The account step of New subscriber, also used alone by Add billing account. */
export interface NewAccount {
  accountName: string;
  cycleDay: number; // 1..28
  currency: string;
  creditLimit: string | null; // canonical decimal string, or none (always none when prepaid)
  ratingType: RatingMode;
}

/** POST one billing account for a customer. Returns the account id. */
export async function createBillingAccount(
  customer: { id: string; name: string },
  form: NewAccount,
): Promise<string> {
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
          id: customer.id,
          name: customer.name,
          '@type': 'PartyRoleRef',
          '@referredType': 'Customer',
        },
      },
    ],
    ...(form.creditLimit === null ? {} : { creditLimit: { unit: form.currency, value: AMOUNT } }),
  };
  const account = await postJson<{ id?: string }>(
    PATHS.accounts,
    form.creditLimit === null ? body : jsonWithAmount(body, form.creditLimit),
  );
  return need(account.id, 'account id');
}

/**
 * Three TMF calls, in the order the references require: the party, the customer engaging
 * it (Active: the operator registers a real customer), then its billing account. TMF
 * defines no composite operation, so a failure undoes what was created, newest first:
 * a failed account DELETEs the customer and then the party, a failed customer the party.
 * If an undo fails too, the error names what is left (with a link to Parties). Returns
 * the account id.
 */
export async function createSubscriber(form: NewSubscriber): Promise<string> {
  const name = partyName(form);
  const kind = form.kind.toLowerCase();
  // newest first: the order to undo in
  const created: (Leftover & { undo: () => Promise<unknown> })[] = [];
  async function step<T>(what: string, call: () => Promise<T>): Promise<T> {
    try {
      return await call();
    } catch (error) {
      const left: Leftover[] = [];
      for (const record of created) {
        if (left.length === 0) {
          try {
            await record.undo();
            continue;
          } catch {
            // falls through: this record and every older one stay
          }
        }
        left.push({ what: record.what, id: record.id });
      }
      const rest = left.length
        ? ` Could not remove: ${left.map((r) => `${r.what} ${r.id}`).join(', ')}.`
        : '';
      throw new SubscriberStepError(`Creating the ${what} failed: ${explain(error)}.${rest}`, left);
    }
  }
  const media = contactMedia({ ...EMPTY_CONTACT, email: form.email, phone: form.phone });
  const party = await step('party', () =>
    postJson<{ id?: string }>(partyPath(form.kind), {
      '@type': form.kind,
      ...partyBody(form),
      ...(media.length ? { contactMedium: media } : {}),
    }),
  );
  const partyId = need(party.id, 'party id');
  created.unshift({
    what: `${kind} ${name}`,
    id: partyId,
    undo: () => request('DELETE', `${partyPath(form.kind)}/${partyId}`),
  });
  const customer = await step('customer', () =>
    postJson<{ id?: string }>(PATHS.customers, {
      '@type': 'Customer',
      name,
      status: 'Active',
      engagedParty: { id: partyId, '@type': 'PartyRef', '@referredType': form.kind },
    }),
  );
  const customerId = need(customer.id, 'customer id');
  created.unshift({
    what: `customer ${name}`,
    id: customerId,
    undo: () => request('DELETE', `${PATHS.customers}/${customerId}`),
  });
  return step('billing account', () => createBillingAccount({ id: customerId, name }, form));
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
  /** the financial account the billing account links to (slice 16), or none */
  financialAccountId: string | null;
  /** the bill structure the account prefers (slice 18) */
  billing: BillingPrefs;
}

export function canonical(cents: number | null): string | null {
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
    financialAccountId: account.financialAccount?.id ?? null,
    billing: account.billing ?? { cycleId: null, formatId: null, mediaIds: [] },
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
  if (after.financialAccountId !== before.financialAccountId) {
    account.financialAccount =
      after.financialAccountId === null
        ? null
        : {
            id: after.financialAccountId,
            '@type': 'FinancialAccountRef',
            '@referredType': 'FinancialAccount',
          };
  }
  const structure = billStructurePatch(before.billing, after.billing);
  if (structure !== null) account.billStructure = structure;
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
  productId?: string,
): Promise<FirstCharge> {
  const query =
    `productOffering.id=${encodeURIComponent(offeringId)}&startDate=${startDate}` +
    (productId === undefined ? '' : `&product.id=${encodeURIComponent(productId)}`);
  return getJson<FirstCharge>(`${PATHS.internal}/${accountId}/firstCharge?${query}`, signal);
}

// --- offerings for Add product ---

type OfferingBody = T620['schemas']['ProductOffering'];
type PriceBody = T620['schemas']['ProductOfferingPrice'];

/** The kind of a prepaid offering: a TMF620 productOfferingCharacteristic `offeringKind`
 * (docs/decisions/prepaid.md); a plan when the characteristic is absent. */
export type OfferingKind = 'plan' | 'addOn' | 'payAsYouGo';
export const OFFERING_KINDS: OfferingKind[] = ['plan', 'addOn', 'payAsYouGo'];
export const KIND_LABEL: Record<OfferingKind, string> = {
  plan: 'Plan',
  addOn: 'Add-on',
  payAsYouGo: 'Pay as you go',
};
const KIND_NAME = 'offeringKind';

type Characteristic = { name?: string; [key: string]: unknown };

export function kindOf(body: { productOfferingCharacteristic?: unknown }): OfferingKind {
  const list = (body.productOfferingCharacteristic ?? []) as {
    name?: string;
    characteristicValueSpecification?: { value?: unknown }[];
  }[];
  const value = list.find((c) => c.name === KIND_NAME)?.characteristicValueSpecification?.[0]
    ?.value;
  return OFFERING_KINDS.find((k) => k === value) ?? 'plan';
}

/** The characteristics of an offering after its kind is set to `kind`: every other entry as
 * loaded; a plan is the default and carries no entry. */
export function withKind(others: Characteristic[], kind: OfferingKind): Characteristic[] {
  const rest = others.filter((c) => c.name !== KIND_NAME);
  if (kind === 'plan') return rest;
  return [
    ...rest,
    {
      '@type': 'CharacteristicSpecification',
      name: KIND_NAME,
      valueType: 'string',
      characteristicValueSpecification: [
        {
          '@type': 'StringCharacteristicValueSpecification',
          valueType: 'string',
          isDefault: true,
          value: kind,
        },
      ],
    },
  ];
}

export interface Offering {
  id: string;
  specificationId?: string | undefined;
  name: string;
  code: string | null;
  specification: string;
  kind: OfferingKind;
  prices: CatalogPrice[]; // display only; the charge itself comes from the preview
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

export const USAGE_UNITS = ['KB', 'MB', 'GB', 'second', 'minute', 'message'] as const;
export type UsageUnit = (typeof USAGE_UNITS)[number];
export const USAGE_UNIT_KIND: Record<UsageUnit, 'data' | 'voice' | 'sms'> = {
  KB: 'data',
  MB: 'data',
  GB: 'data',
  second: 'voice',
  minute: 'voice',
  message: 'sms',
};
const USAGE_BLOCK_SIZE: Record<UsageUnit, number> = {
  KB: 1024,
  MB: 1024 ** 2,
  GB: 1024 ** 3,
  second: 1,
  minute: 60,
  message: 1,
};

export function validUsageBlock(amount: number, units: UsageUnit): boolean {
  const block = amount * USAGE_BLOCK_SIZE[units];
  return Number.isFinite(amount) && amount > 0 && Number.isInteger(block);
}

function toCatalogPrice(price: PriceBody): CatalogPrice {
  return {
    id: need(price.id, 'price id'),
    cents: toCents(need(price.price?.value, 'price value')),
    currency: need(price.price?.unit, 'price unit'),
    interval: chargeInterval(price.recurringChargePeriodType, price.recurringChargePeriodLength),
    unitOfMeasure: price.unitOfMeasure ?? null,
    from: need(price.validFor?.startDateTime, 'price validFor.startDateTime'),
    to: price.validFor?.endDateTime ?? null,
  };
}

/** The offering code: the externalIdentifier entry of type offeringCode. */
export function offeringCode(offering: Pick<OfferingBody, 'externalIdentifier'>): string | null {
  const entry = (offering.externalIdentifier ?? []).find(
    (e) => e.externalIdentifierType === 'offeringCode',
  );
  return entry?.id ?? null;
}

export async function loadSellableOfferings(
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
    kind: kindOf(offering),
    prices: (offering.productOfferingPrice ?? []).flatMap((ref) => {
      const price = ref.id === undefined ? undefined : byId.get(ref.id);
      return price === undefined || price.price?.unit !== currency ? [] : [toCatalogPrice(price)];
    }),
  }));
}

/** The sellable add-on offerings (kind addOn), for Buy add-on. They have no recurring
 * price; the quote with its fees comes from the preview. */
export async function loadAddOnOfferings(signal: AbortSignal): Promise<Offering[]> {
  const offerings = await getAll<OfferingBody>(
    `${PATHS.offerings}?lifecycleStatus=Active&isSellable=true`,
    signal,
  );
  return offerings.flatMap((offering) =>
    kindOf(offering) !== 'addOn'
      ? []
      : [
          {
            id: need(offering.id, 'offering id'),
            name: need(offering.name, 'offering name'),
            code: offeringCode(offering),
            specification: offering.productSpecification?.name ?? '',
            specificationId: offering.productSpecification?.id,
            kind: 'addOn' as const,
            prices: [],
          },
        ],
  );
}

// --- the catalog (TMF620) ---

export type Lifecycle = 'In Design' | 'Active' | 'Retired';
type SpecBody = T620['schemas']['ProductSpecification'];

export interface CatalogPrice {
  id: string;
  cents: number;
  currency: string;
  interval?: string;
  unitOfMeasure: PriceBody['unitOfMeasure'] | null;
  from: string; // instant
  to: string | null; // instant, exclusive; null = open-ended
}

export interface CatalogOffering {
  id: string;
  name: string;
  code: string | null;
  status: Lifecycle;
  isSellable: boolean;
  specification: string;
  specificationId?: string | undefined;
  validFrom: string | null; // instant; null = no validFor (valid at any time)
  validTo: string | null; // instant, exclusive
  prices: CatalogPrice[]; // recurring, attached to this offering
}

export interface Specification {
  id: string;
  name: string;
  description: string | null;
  status: Lifecycle | null;
  brand: string | null;
  version: string | null;
  productNumber: string | null;
  updated: string | null; // lastUpdate, an instant
  resourceSpecification?: SpecBody['resourceSpecification'];
  productSpecCharacteristic?: SpecBody['productSpecCharacteristic'];
}

export function toSpecification(spec: SpecBody): Specification {
  return {
    id: need(spec.id, 'spec id'),
    name: need(spec.name, 'spec name'),
    description: spec.description ?? null,
    status: (spec.lifecycleStatus as Lifecycle | undefined) ?? null,
    brand: spec.brand ?? null,
    version: spec.version ?? null,
    productNumber: spec.productNumber ?? null,
    updated: spec.lastUpdate ?? null,
    resourceSpecification: spec.resourceSpecification,
    productSpecCharacteristic: spec.productSpecCharacteristic,
  };
}

export interface Catalog {
  offerings: CatalogOffering[];
  specifications: Specification[];
}

export async function loadCatalog(signal: AbortSignal): Promise<Catalog> {
  const [offerings, specs, prices] = await Promise.all([
    getAll<OfferingBody>(PATHS.offerings, signal),
    getAll<SpecBody>(PATHS.specifications, signal),
    getAll<PriceBody>(`${PATHS.prices}?priceType=recurring`, signal),
  ]);
  const priceById = new Map(prices.map((price) => [need(price.id, 'price id'), price]));
  return {
    specifications: specs.map(toSpecification),
    offerings: offerings.map((offering) => ({
      id: need(offering.id, 'offering id'),
      name: need(offering.name, 'offering name'),
      code: offeringCode(offering),
      status: need(offering.lifecycleStatus, 'lifecycleStatus') as Lifecycle,
      isSellable: offering.isSellable ?? true,
      specification: offering.productSpecification?.name ?? '',
      specificationId: offering.productSpecification?.id,
      validFrom: offering.validFor?.startDateTime ?? null,
      validTo: offering.validFor?.endDateTime ?? null,
      prices: (offering.productOfferingPrice ?? []).flatMap((ref) => {
        const price = ref.id === undefined ? undefined : priceById.get(ref.id);
        return price === undefined ? [] : [toCatalogPrice(price)];
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
  description: string;
  isSellable: boolean;
  categoryIds: string[];
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
    ...(form.description.trim() === '' ? {} : { description: form.description.trim() }),
    isSellable: form.isSellable,
    category: form.categoryIds.map((id) => ({ id, '@type': 'CategoryRef' })),
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

export interface NewPrice {
  offeringId: string;
  offeringName: string;
  priceType: 'oneTime' | 'recurring' | 'usage';
  amount: string;
  currency: string;
  validFrom: string;
  existingPriceIds: string[];
  unitOfMeasure?: { amount: number; units: UsageUnit };
}

export async function addPrice(form: NewPrice, timeZone: string): Promise<void> {
  const usage = form.priceType === 'usage' ? need(form.unitOfMeasure, 'usage unit') : undefined;
  const suffix =
    form.priceType === 'recurring'
      ? 'monthly'
      : usage === undefined
        ? 'oneTime'
        : `${USAGE_UNIT_KIND[usage.units]} usage`;
  const price = await postJson<PriceBody>(
    PATHS.prices,
    jsonWithAmount(
      {
        '@type': 'ProductOfferingPrice',
        name: `${form.offeringName} ${suffix}`,
        lifecycleStatus: 'Active',
        priceType: form.priceType,
        ...(form.priceType === 'recurring' ? { recurringChargePeriodType: 'monthly' } : {}),
        ...(usage === undefined ? {} : { unitOfMeasure: usage }),
        price: { unit: form.currency, value: AMOUNT },
        validFor: { startDateTime: midnightOf(form.validFrom, timeZone) },
      },
      form.amount,
    ),
  );
  await patchJson(`${PATHS.offerings}/${form.offeringId}`, {
    productOfferingPrice: [...form.existingPriceIds, need(price.id, 'price id')].map((id) => ({
      id,
      '@type': 'ProductOfferingPriceRef',
    })),
  });
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

// --- offering and price detail, edit and delete (TMF620) ---

interface Identifier {
  id?: string;
  externalIdentifierType?: string;
  [key: string]: unknown;
}

export interface OfferingDetail {
  id: string;
  name: string;
  description: string | null;
  status: Lifecycle;
  isSellable: boolean;
  validFrom: string | null; // instant
  validTo: string | null; // instant, exclusive
  code: string | null;
  identifiers: Identifier[]; // every externalIdentifier entry, as loaded
  specification: { id: string; name: string } | null;
  categories: { id: string; name: string }[];
  priceRefs: { id: string; name: string }[]; // every attached price, of any type
  kind: OfferingKind;
  characteristics: Characteristic[]; // every productOfferingCharacteristic, as loaded
  valueUse: { name?: string; [key: string]: unknown }[]; // every prodSpecCharValueUse, as loaded
}

export async function loadOfferingDetail(id: string, signal: AbortSignal): Promise<OfferingDetail> {
  const body = await getJson<OfferingBody>(`${PATHS.offerings}/${id}`, signal);
  const spec = body.productSpecification;
  return {
    id: need(body.id, 'offering id'),
    name: need(body.name, 'offering name'),
    description: body.description ?? null,
    status: need(body.lifecycleStatus, 'lifecycleStatus') as Lifecycle,
    isSellable: body.isSellable ?? true,
    validFrom: body.validFor?.startDateTime ?? null,
    validTo: body.validFor?.endDateTime ?? null,
    code: offeringCode(body),
    identifiers: (body.externalIdentifier ?? []) as Identifier[],
    specification: spec?.id === undefined ? null : { id: spec.id, name: spec.name ?? spec.id },
    categories: (body.category ?? []).flatMap((c) =>
      c.id === undefined ? [] : [{ id: c.id, name: c.name ?? c.id }],
    ),
    priceRefs: (body.productOfferingPrice ?? []).flatMap((p) =>
      p.id === undefined ? [] : [{ id: p.id, name: p.name ?? p.id }],
    ),
    kind: kindOf(body),
    characteristics: (body.productOfferingCharacteristic ?? []) as Characteristic[],
    valueUse: (body.prodSpecCharValueUse ?? []) as OfferingDetail['valueUse'],
  };
}

/** The value an offering narrows a network profile name to ('' = the specification's
 * default), by name. */
export function narrowedProfile(o: Pick<OfferingDetail, 'valueUse'>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const use of o.valueUse) {
    if (!PROFILE_NAMES.includes(use.name ?? '')) continue;
    const first = (use.productSpecCharacteristicValue as { value?: unknown }[] | undefined)?.[0];
    if (first?.value !== undefined) out[use.name as string] = String(first.value);
  }
  return out;
}

/** The values a specification declares for a profile name, the default first. */
export function declaredProfile(spec: Specification | undefined): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [name, text] of Object.entries(networkFields(spec ?? null)))
    if (text !== '') out[name] = text.split(',').map((v) => v.trim());
  return out;
}

async function loadAttachedPrices(
  priceIds: string[],
  priceType: 'oneTime' | 'usage',
  signal: AbortSignal,
): Promise<CatalogPrice[]> {
  if (priceIds.length === 0) return [];
  const prices = await getAll<PriceBody>(`${PATHS.prices}?priceType=${priceType}`, signal);
  return prices.filter((p) => p.id !== undefined && priceIds.includes(p.id)).map(toCatalogPrice);
}

/** The one-time prices (activation fees) among an offering's attached prices. */
export function loadOneTimePrices(priceIds: string[], signal: AbortSignal) {
  return loadAttachedPrices(priceIds, 'oneTime', signal);
}

/** The usage prices among a pay-as-you-go offering's attached prices. */
export function loadUsagePrices(priceIds: string[], signal: AbortSignal) {
  return loadAttachedPrices(priceIds, 'usage', signal);
}

export interface CategoryChoice {
  id: string;
  name: string;
}

export async function loadCategories(signal: AbortSignal): Promise<CategoryChoice[]> {
  const rows = await getAll<{ id?: string; name?: string }>(PATHS.categories, signal);
  return rows.map((c) => ({ id: need(c.id, 'category id'), name: c.name ?? need(c.id, 'id') }));
}

export interface OfferingEditFields {
  name: string;
  description: string;
  isSellable: boolean;
  validFrom: string; // business date, '' = none
  validTo: string; // business date, inclusive last valid day, '' = open-ended
  code: string;
  specificationId: string;
  categoryIds: string[];
  kind: OfferingKind;
  /** narrowed network profile values by name; a name left out uses the default */
  network: Record<string, string>;
}

/** The last valid day of an exclusive end instant, as a business date. */
export function lastValidDate(endExclusive: string, timeZone: string): string {
  return businessDate(new Date(Date.parse(endExclusive) - 1).toISOString(), timeZone);
}

export function offeringEditFields(o: OfferingDetail, timeZone: string): OfferingEditFields {
  return {
    name: o.name,
    description: o.description ?? '',
    isSellable: o.isSellable,
    validFrom: o.validFrom === null ? '' : businessDate(o.validFrom, timeZone),
    validTo: o.validTo === null ? '' : lastValidDate(o.validTo, timeZone),
    code: o.code ?? '',
    specificationId: o.specification?.id ?? '',
    categoryIds: o.categories.map((c) => c.id),
    kind: o.kind,
    network: narrowedProfile(o),
  };
}

/** Only the fields that differ from the loaded offering; null = nothing changed. The
 * code replaces the offeringCode entry and keeps every other identifier as loaded. */
export function offeringChanges(
  before: OfferingDetail,
  form: OfferingEditFields,
  timeZone: string,
): Record<string, unknown> | null {
  const was = offeringEditFields(before, timeZone);
  const changes: Record<string, unknown> = {};
  if (form.name.trim() !== was.name) changes.name = form.name.trim();
  if (form.description.trim() !== was.description)
    changes.description = form.description.trim() === '' ? null : form.description.trim();
  if (form.isSellable !== was.isSellable) changes.isSellable = form.isSellable;
  if (form.validFrom !== was.validFrom || form.validTo !== was.validTo) {
    if (form.validFrom === '' && form.validTo === '') changes.validFor = null;
    else {
      changes.validFor = {
        // an unchanged start is sent as loaded: recomputing it could "move" it
        ...(form.validFrom === ''
          ? {}
          : {
              startDateTime:
                form.validFrom === was.validFrom && before.validFrom !== null
                  ? before.validFrom
                  : midnightOf(form.validFrom, timeZone),
            }),
        ...(form.validTo === ''
          ? {}
          : { endDateTime: midnightOf(addDays(form.validTo, 1), timeZone) }),
      };
    }
  }
  const code = form.code.trim().toUpperCase();
  if (code !== was.code && code !== '') {
    const entry = {
      id: code,
      externalIdentifierType: 'offeringCode',
      '@type': 'ExternalIdentifier',
    };
    const has = before.identifiers.some((i) => i.externalIdentifierType === 'offeringCode');
    changes.externalIdentifier = has
      ? before.identifiers.map((i) =>
          i.externalIdentifierType === 'offeringCode' ? { ...i, id: code } : i,
        )
      : [...before.identifiers, entry];
  }
  if (form.specificationId !== was.specificationId && form.specificationId !== '')
    changes.productSpecification = {
      id: form.specificationId,
      '@type': 'ProductSpecificationRef',
    };
  if ([...form.categoryIds].sort().join() !== [...was.categoryIds].sort().join())
    changes.category = form.categoryIds.map((id) => ({ id, '@type': 'CategoryRef' }));
  const narrowed = (network: Record<string, string>) =>
    JSON.stringify(
      Object.entries(network)
        .filter(([, v]) => v !== '')
        .sort(),
    );
  if (narrowed(form.network) !== narrowed(was.network))
    changes.prodSpecCharValueUse = [
      ...before.valueUse.filter((use) => !PROFILE_NAMES.includes(use.name ?? '')),
      ...Object.entries(form.network)
        .filter(([, value]) => value !== '')
        .sort(([a], [b]) => PROFILE_NAMES.indexOf(a) - PROFILE_NAMES.indexOf(b))
        .map(([name, value]) => ({
          '@type': 'ProductSpecificationCharacteristicValueUse',
          name,
          valueType: 'integer',
          productSpecCharacteristicValue: [
            {
              '@type': 'IntegerCharacteristicValueSpecification',
              valueType: 'integer',
              value: Number(value),
            },
          ],
        })),
    ];
  if (form.kind !== was.kind)
    changes.productOfferingCharacteristic = withKind(before.characteristics, form.kind);
  return Object.keys(changes).length === 0 ? null : changes;
}

export async function patchOffering(id: string, changes: Record<string, unknown>): Promise<void> {
  await patchJson(`${PATHS.offerings}/${id}`, changes);
}

export async function deleteOffering(id: string): Promise<void> {
  await request('DELETE', `${PATHS.offerings}/${id}`);
}

export interface PriceDetail {
  id: string;
  name: string;
  description: string | null;
  priceType: string | null;
  cents: number | null;
  currency: string | null;
  interval: string;
  unitOfMeasure: PriceBody['unitOfMeasure'] | null;
  validFrom: string | null; // instant
  validTo: string | null; // instant, exclusive
}

export async function loadPriceDetail(id: string, signal: AbortSignal): Promise<PriceDetail> {
  const p = await getJson<PriceBody>(`${PATHS.prices}/${id}`, signal);
  return {
    id: need(p.id, 'price id'),
    name: need(p.name, 'price name'),
    description: p.description ?? null,
    priceType: p.priceType ?? null,
    cents: p.price?.value === undefined ? null : toCents(p.price.value),
    currency: p.price?.unit ?? null,
    interval: chargeInterval(p.recurringChargePeriodType, p.recurringChargePeriodLength),
    unitOfMeasure: p.unitOfMeasure ?? null,
    validFrom: p.validFor?.startDateTime ?? null,
    validTo: p.validFor?.endDateTime ?? null,
  };
}

export interface PriceEditFields {
  name: string;
  description: string;
  amount: string; // as typed
}

export interface PriceChange {
  body: Record<string, unknown>;
  amount: string | null; // canonical; goes out as an exact number token
}

/** A merge patch of the changed name, description and amount; null = nothing changed. */
export function priceChanges(
  before: PriceDetail,
  form: PriceEditFields,
  amount: string | null,
): PriceChange | null {
  const body: Record<string, unknown> = {};
  if (form.name.trim() !== before.name) body.name = form.name.trim();
  if (form.description.trim() !== (before.description ?? ''))
    body.description = form.description.trim() === '' ? null : form.description.trim();
  const changedAmount =
    amount !== null && before.currency !== null && Number(amount.replace('.', '')) !== before.cents;
  if (changedAmount) body.price = { unit: before.currency, value: AMOUNT };
  return Object.keys(body).length === 0 ? null : { body, amount: changedAmount ? amount : null };
}

export async function patchPrice(id: string, change: PriceChange): Promise<void> {
  await patchJson(
    `${PATHS.prices}/${id}`,
    change.amount === null ? change.body : jsonWithAmount(change.body, change.amount),
  );
}

/** End a price: validFor keeps its start as loaded and gets `endDay`'s midnight as the
 * exclusive end. */
export async function endPrice(price: PriceDetail, endDay: string, timeZone: string) {
  await patchJson(`${PATHS.prices}/${price.id}`, {
    validFor: {
      startDateTime: need(price.validFrom, 'price validFor.startDateTime'),
      endDateTime: midnightOf(endDay, timeZone),
    },
  });
}

export async function deletePrice(id: string): Promise<void> {
  await request('DELETE', `${PATHS.prices}/${id}`);
}

// --- product specifications (TMF620): records only, billing never reads them ---

/** Text, subscriber-number requirement and descriptive characteristics edited by the UI.
 * An empty optional text is null on edit (merge patch removes it) and left out on create. */
export interface SpecificationFields {
  /** The network profile (docs/decisions/network.md): comma-separated values per profile
   * name, the first the default; '' = the specification does not declare it. */
  network?: Record<string, string>;
  name: string;
  description: string;
  brand: string;
  version: string;
  productNumber: string;
  lifecycleStatus: Lifecycle;
  requiresNumber: boolean;
  requiresImsi?: boolean;
  characteristics: CharacteristicFields[];
}

export interface CharacteristicFields {
  name: string;
  valueType: string;
  description: string;
  configurable: boolean;
  values: string;
  original?: NonNullable<SpecBody['productSpecCharacteristic']>[number];
}

/** The network profile characteristics (app/network/profile.py): integers of zero or more,
 * flags 0 or 1. A specification declaring any makes its offerings network lines. */
export const NETWORK_PROFILE: { name: string; label: string; flag: boolean }[] = [
  { name: 'dataAllowanceMb', label: 'Data allowance (MB)', flag: false },
  { name: 'dataCapMb', label: 'Data cap (MB)', flag: false },
  { name: 'throttleKbps', label: 'Throttle speed (kbps)', flag: false },
  { name: 'voiceMinutes', label: 'Voice minutes', flag: false },
  { name: 'smsCount', label: 'SMS count', flag: false },
  { name: 'hotspot', label: 'Hotspot (0 or 1)', flag: true },
  { name: 'international', label: 'International (0 or 1)', flag: true },
  { name: 'voicemail', label: 'Voicemail (0 or 1)', flag: true },
];
const PROFILE_NAMES = NETWORK_PROFILE.map((p) => p.name);

type SpecCharacteristic = NonNullable<SpecBody['productSpecCharacteristic']>[number];

function valuesOf(c: Partial<SpecCharacteristic>): string {
  return (c.characteristicValueSpecification ?? [])
    .map((v) => String('value' in v ? v.value : ''))
    .join(', ');
}

/** The profile values the specification declares, by name ('' = not declared). */
export function networkFields(spec: Specification | null): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { name } of NETWORK_PROFILE)
    out[name] = valuesOf(
      (spec?.productSpecCharacteristic ?? []).find((c) => c.name === name) ?? {},
    );
  return out;
}

/** Each filled value is an integer, 0 or 1 for a flag; at least one when filled. */
export function validNetwork(network: Record<string, string> | undefined): boolean {
  return NETWORK_PROFILE.every(({ name, flag }) => {
    const text = (network?.[name] ?? '').trim();
    return (
      text === '' ||
      text.split(',').every((value) => {
        const v = value.trim();
        return (
          /^\d+$/.test(v) && Number.isSafeInteger(Number(v)) && (!flag || v === '0' || v === '1')
        );
      })
    );
  });
}

function networkDocuments(
  network: Record<string, string> | undefined,
  before: Specification | null,
) {
  return NETWORK_PROFILE.flatMap(({ name }) => {
    const text = (network?.[name] ?? '').trim();
    if (text === '') return [];
    const original = (before?.productSpecCharacteristic ?? []).find((c) => c.name === name);
    if (
      original !== undefined &&
      valuesOf(original) ===
        text
          .split(',')
          .map((v) => v.trim())
          .join(', ')
    )
      return [original];
    return [
      {
        ...(original ?? {}),
        '@type': 'CharacteristicSpecification',
        name,
        valueType: 'integer',
        configurable: false,
        characteristicValueSpecification: text.split(',').map((value, index) => ({
          '@type': 'IntegerCharacteristicValueSpecification',
          valueType: 'integer',
          isDefault: index === 0,
          value: Number(value.trim()),
        })),
      },
    ];
  });
}

function allCharacteristicDocuments(form: SpecificationFields, before: Specification | null) {
  return [
    ...characteristicDocuments(form.characteristics),
    ...networkDocuments(form.network, before),
  ];
}

export function characteristicFields(spec: Specification | null): CharacteristicFields[] {
  return (spec?.productSpecCharacteristic ?? [])
    .filter((c) => !PROFILE_NAMES.includes(c.name ?? ''))
    .map((c) => ({
      name: c.name ?? '',
      valueType: c.valueType ?? 'string',
      description: c.description ?? '',
      configurable: c.configurable ?? false,
      values: (c.characteristicValueSpecification ?? [])
        .map((v) => String('value' in v ? v.value : ''))
        .join(', '),
      original: c,
    }));
}

export function characteristicDocuments(rows: CharacteristicFields[]) {
  return rows.map((row) => {
    if (row.original) {
      const initial = characteristicFields({
        productSpecCharacteristic: [row.original],
      } as Specification)[0];
      if (JSON.stringify(row) === JSON.stringify(initial)) return row.original;
    }
    return {
      ...row.original,
      '@type': row.original?.['@type'] ?? 'CharacteristicSpecification',
      name: row.name.trim(),
      description: row.description.trim(),
      valueType: row.valueType,
      configurable: row.configurable,
      characteristicValueSpecification:
        row.values.trim() === ''
          ? []
          : row.values.split(',').map((text, index) => ({
              '@type':
                row.valueType === 'boolean'
                  ? 'BooleanCharacteristicValueSpecification'
                  : row.valueType === 'integer'
                    ? 'IntegerCharacteristicValueSpecification'
                    : 'StringCharacteristicValueSpecification',
              valueType: row.valueType,
              isDefault: index === 0,
              value:
                row.valueType === 'boolean'
                  ? text.trim() === 'true'
                  : row.valueType === 'integer'
                    ? Number(text.trim())
                    : text.trim(),
            })),
    };
  });
}

const SPEC_TEXT = ['description', 'brand', 'version', 'productNumber'] as const;

export async function createSpecification(form: SpecificationFields): Promise<Specification> {
  const body: Record<string, unknown> = {
    '@type': 'ProductSpecification',
    name: form.name.trim(),
    lifecycleStatus: form.lifecycleStatus,
  };
  for (const key of SPEC_TEXT) if (form[key].trim()) body[key] = form[key].trim();
  const required = [form.requiresNumber && 'msisdn', form.requiresImsi && 'imsi'].filter(Boolean);
  if (required.length)
    body.resourceSpecification = required.map((id) => ({
      id,
      '@type': 'ResourceSpecificationRef',
    }));
  const documents = allCharacteristicDocuments(form, null);
  if (documents.length) body.productSpecCharacteristic = documents;
  return toSpecification(await postJson<SpecBody>(PATHS.specifications, body));
}

/** Only the fields that differ from `before`; null = nothing changed. */
export function specificationChanges(
  before: Specification,
  form: SpecificationFields,
): Record<string, unknown> | null {
  const changes: Record<string, unknown> = {};
  if (form.name.trim() !== before.name) changes.name = form.name.trim();
  for (const key of SPEC_TEXT) {
    const now = form[key].trim();
    if (now !== (before[key] ?? '')) changes[key] = now === '' ? null : now;
  }
  if (form.lifecycleStatus !== before.status) changes.lifecycleStatus = form.lifecycleStatus;
  const had = (id: string) => (before.resourceSpecification ?? []).some((ref) => ref.id === id);
  const wanted = { msisdn: form.requiresNumber, imsi: form.requiresImsi ?? had('imsi') };
  if (had('msisdn') !== wanted.msisdn || had('imsi') !== wanted.imsi)
    changes.resourceSpecification = [
      ...(before.resourceSpecification ?? []).filter(
        (ref) => ref.id !== 'msisdn' && ref.id !== 'imsi',
      ),
      ...(['msisdn', 'imsi'] as const)
        .filter((id) => wanted[id])
        .map((id) => ({ id, '@type': 'ResourceSpecificationRef' })),
    ];
  const networkChanged =
    form.network !== undefined &&
    JSON.stringify(NETWORK_PROFILE.map((p) => (form.network?.[p.name] ?? '').trim())) !==
      JSON.stringify(NETWORK_PROFILE.map((p) => networkFields(before)[p.name]));
  if (
    networkChanged ||
    JSON.stringify(form.characteristics) !== JSON.stringify(characteristicFields(before))
  ) {
    // keep the entries the form does not edit (and the unchanged profile ones) as loaded
    changes.productSpecCharacteristic = allCharacteristicDocuments(
      { ...form, network: form.network ?? networkFields(before) },
      before,
    );
  }
  return Object.keys(changes).length === 0 ? null : changes;
}

export async function patchSpecification(
  id: string,
  changes: Record<string, unknown>,
): Promise<void> {
  await patchJson(`${PATHS.specifications}/${id}`, changes);
}

export async function deleteSpecification(id: string): Promise<void> {
  await request('DELETE', `${PATHS.specifications}/${id}`);
}

// --- orders (TMF622): the only way to add or end a product ---

type OrderBody = T622['schemas']['ProductOrder'];

export interface OrderOutcome {
  id: string;
  state: string; // completed | inProgress (prepaid: the worker finishes it) | rejected
  errors: OrderError[];
  refusal: ReturnType<typeof orderRefusal>;
}

function outcome(order: OrderBody): OrderOutcome {
  return {
    id: order.id ?? '', // always sent; only an inProgress order is followed by it
    state: need(order.state, 'order state'),
    errors: order.productOrderErrorMessage ?? [],
    refusal: orderRefusal(order.productOrderErrorMessage ?? [], order.state),
  };
}

/** The requested date as noon of that day in BILLING_TZ, with BILLING_TZ's offset on that
 * date: the backend takes the calendar date in BILLING_TZ. Never the browser's zone. */
export function requestedDate(day: string, timeZone: string): string {
  return zonedDateTime(day, '12:00', timeZone);
}

export type { SimType };

/** The product part of an add item: the chosen resources, or (instead of an IMSI) the
 * `simType` characteristic that lets the server pick a SIM of that type from stock. */
function addProduct(numberId: string | string[] | undefined, simType?: SimType) {
  if (!numberId && !simType) return {};
  return {
    product: {
      ...(numberId ? numberProduct(numberId) : { '@type': 'ProductRefOrValue' }),
      ...(simType
        ? {
            productCharacteristic: [
              {
                '@type': 'StringCharacteristic',
                name: 'simType',
                valueType: 'string',
                value: simType,
              },
            ],
          }
        : {}),
    },
  };
}

function addItem(
  offeringId: string,
  numberId: string | string[] | undefined,
  id = '1',
  simType?: SimType,
) {
  return {
    '@type': 'ProductOrderItem',
    id,
    action: 'add',
    productOffering: { id: offeringId, '@type': 'ProductOfferingRef' },
    ...addProduct(numberId, simType),
  };
}

/** Place an add order. With `draftKey` it is saved as a draft instead (TMF622
 * requestedInitialState "draft", under that Idempotency-Key): nothing is processed. */
export async function orderAdd(
  accountId: string,
  offeringId: string,
  startDate: string,
  timeZone: string,
  numberId?: string | string[],
  draftKey?: string,
  simType?: SimType,
): Promise<OrderOutcome> {
  const order = await postJson<OrderBody>(
    PATHS.orders,
    {
      '@type': 'ProductOrder',
      billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
      requestedStartDate: requestedDate(startDate, timeZone),
      ...(draftKey !== undefined ? { requestedInitialState: 'draft' } : {}),
      productOrderItem: [addItem(offeringId, numberId, '1', simType)],
    },
    draftKey !== undefined ? { 'Idempotency-Key': draftKey } : {},
  );
  return outcome(order);
}

/** Buy an add-on for a plan product (TMF622 add item with a `productRelationship` of type
 * addOn). Add-ons start today; the server decides every rule and answers with its codes. */
export async function orderAddOn(
  accountId: string,
  offeringId: string,
  planProductId: string,
  day: string,
  timeZone: string,
): Promise<OrderOutcome> {
  const order = await postJson<OrderBody>(PATHS.orders, {
    '@type': 'ProductOrder',
    billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
    requestedStartDate: requestedDate(day, timeZone),
    productOrderItem: [
      {
        '@type': 'ProductOrderItem',
        id: '1',
        action: 'add',
        productOffering: { id: offeringId, '@type': 'ProductOfferingRef' },
        product: {
          '@type': 'ProductRefOrValue',
          productRelationship: [
            { '@type': 'ProductRelationship', relationshipType: 'addOn', id: planProductId },
          ],
        },
      },
    ],
  });
  return outcome(order);
}

/** Replace a draft's item and start date (TMF622 PATCH, each replaced whole). */
export async function replaceDraft(
  orderId: string,
  offeringId: string,
  startDate: string,
  timeZone: string,
  numberId: string | string[] | undefined,
  itemId: string,
  simType?: SimType,
): Promise<OrderOutcome> {
  const order = await patchJson<OrderBody>(`${PATHS.orders}/${orderId}`, {
    productOrderItem: [addItem(offeringId, numberId, itemId, simType)],
    requestedStartDate: requestedDate(startDate, timeZone),
  });
  return outcome(order);
}

/** Submit a draft: it is processed as an order placed now (completed, inProgress or rejected). */
export async function submitDraft(orderId: string): Promise<OrderOutcome> {
  return outcome(
    await patchJson<OrderBody>(`${PATHS.orders}/${orderId}`, { state: 'acknowledged' }),
  );
}

/** Delete a draft or rejected order (nothing refers to it). */
export async function removeOrder(orderId: string): Promise<void> {
  await request('DELETE', `${PATHS.orders}/${orderId}`);
}

/** Only the changed descriptive attributes; null removes one (JSON Merge Patch). */
export interface OrderDetails {
  description?: string | null;
  priority?: string | null;
  category?: string | null;
}

export async function patchOrderDetails(orderId: string, changes: OrderDetails): Promise<void> {
  await patchJson(`${PATHS.orders}/${orderId}`, changes);
}

/** Cancel an order (TMF622 cancelProductOrder), with an optional reason. The server decides
 * whether it can be cancelled (409 with its reasons). */
export async function cancelOrder(orderId: string, reason?: string): Promise<void> {
  await postJson(PATHS.cancellations, {
    '@type': 'CancelProductOrder',
    productOrder: { id: orderId, '@type': 'ProductOrderRef' },
    ...(reason ? { cancellationReason: reason } : {}),
  });
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

export type ProductChange = 'suspend' | 'resume' | 'end';

/** Suspend, resume or end a product on `day` (a business date): one TMF622 item,
 * `requestedStartDate` as noon of that day in BILLING_TZ, like the add order. A status
 * change is a modify item with product.status; ending is a delete item. */
export async function orderProductChange(
  accountId: string,
  productId: string,
  change: ProductChange,
  day: string,
  timeZone: string,
): Promise<OrderOutcome> {
  const ref = { id: productId, '@type': 'ProductRef' };
  const order = await postJson<OrderBody>(PATHS.orders, {
    '@type': 'ProductOrder',
    billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
    requestedStartDate: requestedDate(day, timeZone),
    productOrderItem: [
      change === 'end'
        ? { '@type': 'ProductOrderItem', id: '1', action: 'delete', product: ref }
        : {
            '@type': 'ProductOrderItem',
            id: '1',
            action: 'modify',
            product: { ...ref, status: change === 'suspend' ? 'suspended' : 'active' },
          },
    ],
  });
  return outcome(order);
}

/** Change a product's offering on `day`: one modify item with the new offering and no status
 * (a status in the same item is a 422 by rule). The offering ref carries its catalog href. */
export async function orderOfferingChange(
  accountId: string,
  productId: string,
  offeringId: string,
  day: string,
  timeZone: string,
): Promise<OrderOutcome> {
  const order = await postJson<OrderBody>(PATHS.orders, {
    '@type': 'ProductOrder',
    billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
    requestedStartDate: requestedDate(day, timeZone),
    productOrderItem: [
      {
        '@type': 'ProductOrderItem',
        id: '1',
        action: 'modify',
        product: { id: productId, '@type': 'ProductRef' },
        productOffering: {
          id: offeringId,
          href: `${PATHS.offerings}/${offeringId}`,
          '@type': 'ProductOfferingRef',
        },
      },
    ],
  });
  return outcome(order);
}

/** Undo a pending change: cancel (TMF622 cancelProductOrder) the order that holds it. */
export async function undoPendingChange(orderId: string): Promise<void> {
  await postJson(PATHS.cancellations, {
    '@type': 'CancelProductOrder',
    productOrder: { id: orderId, '@type': 'ProductOrderRef' },
  });
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
  cycleId?: string | undefined; // the bill cycle (daily run) that issued it
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
    cycleId: body.billCycle?.id,
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
  correlatorId?: string;
  description?: string;
}

/** TMF676 POST /payment with exact amount tokens. Returns how many bills it paid now. */
export async function recordPayment(accountId: string, form: PaymentForm): Promise<number> {
  const body = {
    '@type': 'Payment',
    account: { id: accountId, '@referredType': 'BillingAccount' },
    totalAmount: { unit: form.currency, value: amountSlot(0) },
    paymentMethod: { name: form.method },
    ...(form.correlatorId?.trim() ? { correlatorId: form.correlatorId.trim() } : {}),
    ...(form.description?.trim() ? { description: form.description.trim() } : {}),
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

/** How many runs each history page shows. */
export const RUN_HISTORY = 20;

/** The newest runs first; `total` is every run there is. */
export function loadRuns(
  signal: AbortSignal,
  limit = RUN_HISTORY,
  offset?: number,
): Promise<Page<BillingRun>> {
  const query = offset === undefined ? `limit=${limit}` : `offset=${offset}&limit=${limit}`;
  return getPage<BillingRun>(`${PATHS.runs}?${query}`, signal);
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
