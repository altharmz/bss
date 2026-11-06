import type { components as Internal } from './generated/internal';
import {
  AMOUNT,
  getAll,
  getJson,
  amountSlot,
  jsonWithAmount,
  jsonWithAmounts,
  newIdempotencyKey,
  patchJson,
  postJson,
  request,
} from './client';

// Saved cards (TMF670 v4, no generated types: Swagger 2.0), card top-ups and the auto top-up
// rule (internal API). docs/decisions/payments.md. The UI never holds a card number: a card
// is saved with a stub token, its brand, last four digits and expiry.

const METHODS = '/tmf-api/paymentMethod/v4/paymentMethod';
const BILLING = '/internal/v1/billingAccount';
const COMMANDS = '/internal/v1/payment/command';

export interface SavedCard {
  id: string;
  name: string;
  brand: string | null;
  lastFourDigits: string | null;
  expirationDate: string | null;
  isPreferred: boolean;
}

interface CardBody {
  id: string;
  name?: string;
  brand?: string;
  lastFourDigits?: string;
  expirationDate?: string;
  isPreferred?: boolean;
}

function card(body: CardBody): SavedCard {
  return {
    id: body.id,
    name: body.name ?? '',
    brand: body.brand ?? null,
    lastFourDigits: body.lastFourDigits ?? null,
    expirationDate: body.expirationDate ?? null,
    isPreferred: body.isPreferred === true,
  };
}

export async function loadCards(accountId: string, signal: AbortSignal): Promise<SavedCard[]> {
  const rows = await getAll<CardBody>(`${METHODS}?account.id=${accountId}`, signal);
  return rows.map(card);
}

export const CARD_BRANDS = ['visa', 'mastercard', 'mir', 'amex', 'other'] as const;

export interface NewCard {
  accountId: string;
  name: string;
  brand: string;
  lastFourDigits: string;
  /** Expiry month, 1-12, and four-digit year. */
  month: number;
  year: number;
}

/** A card is valid through the end of its month: the stored instant is the start of the next. */
export function expiryInstant(month: number, year: number): string {
  const next = month === 12 ? [year + 1, 1] : [year, month + 1];
  return `${String(next[0]).padStart(4, '0')}-${String(next[1]).padStart(2, '0')}-01T00:00:00Z`;
}

/** The stub token stands in for the gateway's client-side form: letters and a UUID, never a
 * card number (the API refuses digits-only tokens by shape). */
export function stubToken(): string {
  return `tok_stub_${newIdempotencyKey()}`;
}

export async function createCard(input: NewCard, token: string): Promise<SavedCard> {
  return card(
    await postJson<CardBody>(METHODS, {
      '@type': 'tokenizedCard',
      name: input.name,
      account: [{ id: input.accountId, '@referredType': 'BillingAccount' }],
      brand: input.brand,
      lastFourDigits: input.lastFourDigits,
      expirationDate: expiryInstant(input.month, input.year),
      token,
    }),
  );
}

export async function makeDefault(id: string): Promise<SavedCard> {
  return card(await patchJson<CardBody>(`${METHODS}/${id}`, { isPreferred: true }));
}

export async function removeCard(id: string): Promise<void> {
  await request('DELETE', `${METHODS}/${id}`);
}

export type PaymentCommand = Internal['schemas']['PaymentCommand'];
export type CommandState = PaymentCommand['state'];
export type AutoTopup = Internal['schemas']['AutoTopup'];

/** The card charges of the account, newest first (one page is enough for the dialog). */
export function loadCardTopups(accountId: string, signal: AbortSignal): Promise<PaymentCommand[]> {
  return getJson<PaymentCommand[]>(`${BILLING}/${accountId}/cardTopup?limit=10`, signal);
}

/** Queue a card top-up (202; the balance rises only when the gateway confirms). The body
 * carries its own idempotency key: the same key sent again is the same charge. */
export function submitCardTopup(
  accountId: string,
  amount: string,
  currency: string,
  cardId: string,
  key: string,
): Promise<PaymentCommand> {
  return postJson<PaymentCommand>(
    `${BILLING}/${accountId}/cardTopup`,
    jsonWithAmount(
      {
        amount: { unit: currency, value: AMOUNT },
        paymentMethod: { id: cardId },
        idempotencyKey: key,
      },
      amount,
    ),
  );
}

export async function retryPayment(id: string): Promise<PaymentCommand> {
  const response = await request('POST', `${COMMANDS}/${id}/retry`);
  return (await response.json()) as PaymentCommand;
}

export function loadAutoTopup(accountId: string, signal: AbortSignal): Promise<AutoTopup> {
  return getJson<AutoTopup>(`${BILLING}/${accountId}/autoTopup`, signal);
}

/** Save the rule. Turning it off sends only `enabled: false` (the values are kept). */
export function saveAutoTopup(
  accountId: string,
  rule: { enabled: false } | { enabled: true; threshold: string; amount: string; currency: string },
): Promise<AutoTopup> {
  const path = `${BILLING}/${accountId}/autoTopup`;
  if (!rule.enabled) return patchJson<AutoTopup>(path, { enabled: false });
  return patchJson<AutoTopup>(
    path,
    jsonWithAmounts(
      {
        enabled: true,
        threshold: { unit: rule.currency, value: amountSlot(0) },
        amount: { unit: rule.currency, value: amountSlot(1) },
      },
      [rule.threshold, rule.amount],
    ),
  );
}
