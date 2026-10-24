import { PATHS } from './bss';
import { getAll, getJson } from './client';
import { toCents } from '../format/money';

// TMF654 balanceActionHistory: every top-up, hold, adjustment and transfer of a prepaid
// account, read-only (docs/decisions/frontend.md, slice 2 of docs/next-slices.md).

const HISTORY = '/tmf-api/prepayBalanceManagement/v4/balanceActionHistory';

export type ActionKind = 'topup' | 'reservation' | 'adjustment' | 'transfer';

const KIND_OF: Record<string, ActionKind> = {
  TopupBalance: 'topup',
  ReserveBalance: 'reservation',
  AdjustBalance: 'adjustment',
  TransferBalance: 'transfer',
};

interface Ref {
  id?: string;
  name?: string;
}

/** The balanceActionHistory body, reduced to what the screen reads. */
interface HistoryBody {
  id?: string;
  '@type'?: string;
  status?: string;
  amount?: { amount?: number; units?: string };
  requestedDate?: string;
  confirmationDate?: string;
  reason?: string;
  description?: string;
  partyAccount?: Ref;
  bucket?: Ref;
  receiverBucket?: Ref;
  transferCost?: { value?: number; unit?: string };
  costOwner?: string;
  paymentMethod?: { name?: string; id?: string };
  validFor?: { endDateTime?: string };
  product?: Ref;
  receiverLogicalResource?: Ref;
}

interface BucketBody {
  partyAccount?: Ref;
}

function need<T>(value: T | undefined | null, what: string): T {
  if (value === undefined || value === null) throw new Error(`the API sent no ${what}`);
  return value;
}

/** One balance action as the account sees it. */
export interface BalanceAction {
  id: string;
  kind: ActionKind;
  status: string;
  /** Signed from this account's view; a hold is unsigned (it moves nothing). */
  cents: number;
  requestedDate: string;
  confirmationDate: string | null;
  reason: string | null;
  description: string | null;
  /** A transfer: this account sent it (otherwise it received it). */
  sent: boolean;
  sender: { id: string; name: string | null };
  receiver: { id: string; name: string | null } | null;
  paymentMethod: string | null;
  expiry: string | null;
  transferCostCents: number | null;
  costOwner: string | null;
  product: { id: string; name: string | null } | null;
}

export function toAction(
  body: HistoryBody,
  accountId: string,
  receiver: { id: string; name: string | null } | null,
): BalanceAction {
  const type = need(body['@type'], '@type');
  const kind = KIND_OF[type];
  if (kind === undefined) throw new Error(`unknown balance action type ${type}`);
  const amount = toCents(need(body.amount?.amount, 'amount'));
  const sender = need(body.partyAccount, 'partyAccount');
  const sent = sender.id === accountId;
  let cents = amount;
  if (kind === 'transfer') cents = sent ? -amount : amount;
  return {
    id: need(body.id, 'id'),
    kind,
    status: need(body.status, 'status'),
    cents,
    requestedDate: need(body.requestedDate, 'requestedDate'),
    confirmationDate: body.confirmationDate ?? null,
    reason: body.reason ?? null,
    description: body.description ?? null,
    sent,
    sender: { id: need(sender.id, 'partyAccount id'), name: sender.name ?? null },
    receiver,
    paymentMethod: body.paymentMethod?.name ?? body.paymentMethod?.id ?? null,
    expiry: body.validFor?.endDateTime ?? null,
    transferCostCents:
      kind === 'transfer' && body.transferCost?.value !== undefined
        ? toCents(body.transferCost.value)
        : null,
    costOwner: kind === 'transfer' ? (body.costOwner ?? null) : null,
    product: body.product?.id ? { id: body.product.id, name: body.product.name ?? null } : null,
  };
}

/** Who owns a bucket: the transfer body names the receiving bucket only. */
async function bucketOwner(
  bucketId: string,
  signal: AbortSignal,
): Promise<{ id: string; name: string | null } | null> {
  try {
    const bucket = await getJson<BucketBody>(`${PATHS.buckets}/${bucketId}`, signal);
    return bucket.partyAccount?.id
      ? { id: bucket.partyAccount.id, name: bucket.partyAccount.name ?? null }
      : null;
  } catch (error) {
    if (signal.aborted) throw error;
    return null;
  }
}

async function withReceivers(
  bodies: HistoryBody[],
  accountId: string,
  signal: AbortSignal,
): Promise<BalanceAction[]> {
  // only a transfer this account sent names a receiver we do not know
  const sentBy = (b: HistoryBody) => b.partyAccount?.id === accountId;
  const buckets = [
    ...new Set(
      bodies.flatMap((b) => (sentBy(b) && b.receiverBucket?.id ? [b.receiverBucket.id] : [])),
    ),
  ];
  const owners = new Map(
    await Promise.all(buckets.map(async (id) => [id, await bucketOwner(id, signal)] as const)),
  );
  return bodies.map((body) =>
    toAction(
      body,
      accountId,
      sentBy(body)
        ? (owners.get(body.receiverBucket?.id ?? '') ?? null)
        : { id: accountId, name: null },
    ),
  );
}

/** The account's own actions plus the transfers it received, merged, de-duplicated by id
 * and newest requested first. */
export async function loadBalanceActions(
  accountId: string,
  bucketId: string,
  signal: AbortSignal,
): Promise<BalanceAction[]> {
  const [own, received] = await Promise.all([
    getAll<HistoryBody>(`${HISTORY}?partyAccount.id=${accountId}`, signal),
    getAll<HistoryBody>(`${HISTORY}?receiverBucket.id=${bucketId}`, signal),
  ]);
  const byId = new Map<string, HistoryBody>();
  for (const body of [...own, ...received]) byId.set(need(body.id, 'id'), body);
  const actions = await withReceivers([...byId.values()], accountId, signal);
  return actions.sort((a, b) => b.requestedDate.localeCompare(a.requestedDate));
}

export async function loadBalanceAction(
  id: string,
  accountId: string,
  signal: AbortSignal,
): Promise<BalanceAction> {
  const body = await getJson<HistoryBody>(`${HISTORY}/${id}`, signal);
  const [action] = await withReceivers([body], accountId, signal);
  return need(action, 'balance action');
}
