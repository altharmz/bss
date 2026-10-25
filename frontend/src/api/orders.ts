import type { components as T622 } from './generated/tmf622';
import type { PrepaidCommand } from './commands';
import { getAll, getPage } from './client';
import { PATHS } from './bss';

// The global Orders screen (docs/decisions/frontend.md, docs/next-slices-design.md section 9):
// TMF622 reads only. The server serves orders oldest first; there is no sort parameter.

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
export const ORDER_PAGE = 50;

/** One page of orders; `total` is every matching row (X-Total-Count). */
export function loadOrderPage(
  filter: { state: OrderState | null; account: string | null },
  offset: number,
  signal: AbortSignal,
) {
  const query = [
    ...(filter.account !== null ? [`billingAccount.id=${filter.account}`] : []),
    ...(filter.state !== null ? [`state=${filter.state}`] : []),
    `offset=${offset}`,
    `limit=${ORDER_PAGE}`,
  ];
  return getPage<Order>(`${PATHS.orders}?${query.join('&')}`, signal);
}

/** The order's cancellations (TMF622 cancelProductOrder), for its dialog. */
export function loadCancellations(orderId: string, signal: AbortSignal) {
  return getAll<Cancellation>(`${PATHS.cancellations}?productOrder.id=${orderId}`, signal);
}

/** The order count of the sidebar item. */
export function countOrders(signal: AbortSignal): Promise<number> {
  return getPage<Order>(`${PATHS.orders}?limit=1&fields=id`, signal).then((page) => page.total);
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
