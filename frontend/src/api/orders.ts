import type { components as T622 } from './generated/tmf622';
import type { PrepaidCommand } from './commands';
import { getAll, getPage, getJson } from './client';
import { PATHS } from './bss';

// The global Orders screen (docs/decisions/frontend.md, docs/ui.md):
// TMF622 reads only. The server serves orders newest first; there is no sort parameter.

export type Order = T622['schemas']['ProductOrder'];
export type OrderItem = T622['schemas']['ProductOrderItem'];
export type OrderState = NonNullable<Order['state']>;
export type Cancellation = T622['schemas']['CancelProductOrder'];

/** The states this service reaches (app/ordering/states.py); the filter offers these. */
export const ORDER_STATES: OrderState[] = [
  'draft',
  'acknowledged',
  'inProgress',
  'completed',
  'rejected',
  'cancelled',
  'failed',
];
/** The Orders screen's default (#455): orders someone may still have to act on. `failed`
 * counts: it needs a person. The API takes them as one comma-separated `state` (OR). */
export const UNFINISHED_STATES: readonly OrderState[] = [
  'draft',
  'acknowledged',
  'inProgress',
  'failed',
];
export const ORDER_PAGE = 50;

type StateQuery = OrderState | readonly OrderState[] | null;

/** One page of orders, newest first; `more` says whether older ones follow. */
export function loadOrderPage(
  filter: { state: StateQuery; account: string | null },
  offset: number,
  signal: AbortSignal,
) {
  const query = [
    ...(filter.account !== null ? [`billingAccount.id=${filter.account}`] : []),
    ...(filter.state !== null
      ? [`state=${typeof filter.state === 'string' ? filter.state : filter.state.join(',')}`]
      : []),
    `offset=${offset}`,
    `limit=${ORDER_PAGE}`,
  ];
  return getPage<Order>(`${PATHS.orders}?${query.join('&')}`, signal);
}

/** The API serves orders newest first (#312), so the newest page is the first page. */
export function loadNewestOrderPage(
  filter: { state: StateQuery; account: string | null },
  offset: number,
  signal: AbortSignal,
) {
  return loadOrderPage(filter, offset, signal);
}

export function loadOrder(id: string) {
  return getJson<Order>(`${PATHS.orders}/${id}`);
}

/** The order's cancellations (TMF622 cancelProductOrder), for its dialog. */
export function loadCancellations(orderId: string, signal: AbortSignal) {
  return getAll<Cancellation>(`${PATHS.cancellations}?productOrder.id=${orderId}`, signal);
}

/** The prepaid command of an in-progress order, if the account has one: the journal is
 * asked by account and matched on the command's order link. */
export async function findOrderCommand(
  orderId: string,
  accountId: string,
  signal: AbortSignal,
): Promise<PrepaidCommand | null> {
  const commands = await getAll<PrepaidCommand>(
    `/internal/v1/prepaid/command?billingAccount.id=${accountId}`,
    signal,
  );
  return commands.find((c) => c.productOrder?.id === orderId) ?? null;
}
