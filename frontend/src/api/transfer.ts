import type { components as T637 } from './generated/tmf637';
import { loadAccount, loadPrepaidBalance, PATHS, type Account, type PrepaidBalance } from './bss';
import { AMOUNT, getAll, getJson, jsonWithAmount, postJson } from './client';
import { NUMBERS, loadNumberProducts, type SubscriberNumber } from './numbers';

// Balance transfer between two prepaid subscribers, named by their numbers (TMF654
// transferBalance, docs/decisions/prepaid.md P4g-P4h, frontend.md P6c1).

const TRANSFERS = '/tmf-api/prepayBalanceManagement/v4/transferBalance';

type Product = T637['schemas']['Product'];

/** One of the sender's own numbers. */
export interface OwnNumber {
  id: string;
  value: string;
}

export async function loadOwnNumbers(accountId: string, signal: AbortSignal): Promise<OwnNumber[]> {
  const products = await getAll<Product>(
    `${PATHS.products}?billingAccount.id=${accountId}&status=active`,
    signal,
  );
  const ids = [...new Set(products.flatMap((p) => (p.realizingResource ?? []).map((r) => r.id)))];
  const numbers = await Promise.all(
    ids.map((id) => getJson<SubscriberNumber>(`${NUMBERS}/${id}`, signal)),
  );
  return numbers.map((n) => ({ id: n.id, value: n.value }));
}

/** Who a typed number reaches: its number, the account of the product that uses it, and
 * that account's bucket (absent on a postpaid account: it cannot receive). */
export interface Receiver {
  numberId: string;
  digits: string;
  account: Account;
  balance: PrepaidBalance | null;
}

export type ReceiverLookup = { found: true; receiver: Receiver } | { found: false; why: string };

export async function findReceiver(digits: string, signal: AbortSignal): Promise<ReceiverLookup> {
  const numbers = await getAll<SubscriberNumber>(
    `${NUMBERS}?value=${encodeURIComponent(digits)}`,
    signal,
  );
  const number = numbers[0];
  if (number === undefined)
    return { found: false, why: `${digits} is not in the number inventory.` };
  const products = await loadNumberProducts(number.id, signal);
  const used = products.find((p) => p.status !== 'terminated' && p.status !== 'cancelled');
  const accountId = used?.billingAccount?.id;
  if (accountId === undefined)
    return { found: false, why: `${digits} is not used by any subscriber.` };
  const account = await loadAccount(accountId, signal);
  if (account.ratingType !== 'prepaid') {
    return { found: true, receiver: { numberId: number.id, digits, account, balance: null } };
  }
  const balance = await loadPrepaidBalance(accountId, signal);
  return { found: true, receiver: { numberId: number.id, digits, account, balance } };
}

export interface TransferForm {
  senderNumberId: string;
  receiverNumberId: string;
  bucketId: string;
  receiverBucketId: string;
  currency: string;
  amount: string; // canonical decimal string, positive
  reason: string; // required by the API
}

/** The exact request the form sends: path and JSON text (the amount as a verbatim token).
 * No transferCost: absent means 0.00 paid by the originator. */
export function transferRequest(form: TransferForm) {
  return {
    path: TRANSFERS,
    body: jsonWithAmount(
      {
        '@type': 'TransferBalance',
        reason: form.reason.trim(),
        channel: { id: 'admin-ui', name: 'Admin UI' },
        logicalResource: [{ id: form.senderNumberId }],
        receiverLogicalResource: { id: form.receiverNumberId },
        amount: { amount: AMOUNT, units: form.currency },
        usageType: 'monetary',
        bucket: { id: form.bucketId },
        receiverBucket: { id: form.receiverBucketId },
        receiverBucketUsageType: 'monetary',
      },
      form.amount,
    ),
  };
}

/** A transfer's status: `created` (accepted, money held, waiting for the worker),
 * `completed` or `failed`. A refusal is a stored 201 whose `description` says why
 * (`reason` keeps the operator's own words). */
export interface TransferState {
  id: string;
  status: string;
  description: string | null;
}

function toState(body: { id?: string; status?: string; description?: string }): TransferState {
  if (!body.id || !body.status) throw new Error('the API sent a transfer without id or status');
  return { id: body.id, status: body.status, description: body.description ?? null };
}

export async function submitTransfer(
  request: { path: string; body: string },
  key: string,
): Promise<TransferState> {
  return toState(
    await postJson<{ id?: string; status?: string; description?: string }>(
      request.path,
      request.body,
      { 'Idempotency-Key': key },
    ),
  );
}

export async function loadTransfer(id: string, signal: AbortSignal): Promise<TransferState> {
  return toState(
    await getJson<{ id?: string; status?: string; description?: string }>(
      `${TRANSFERS}/${id}`,
      signal,
    ),
  );
}
