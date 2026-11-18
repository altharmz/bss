import type { components as Internal } from './generated/internal';
import { getAll, request } from './client';

// Network provisioning commands (internal API, docs/decisions/network.md): what the billing
// worker sends to the network for an order item. The UI reads them per order or product and
// has one write: Retry of an `exhausted` command. A refusal is final (no retry).

const NETWORK = '/internal/v1/network/command';

export type NetworkCommand = Internal['schemas']['NetworkCommand'];
export type NetworkState = NetworkCommand['state'];
export type NetworkKind = NetworkCommand['kind'];

export const NETWORK_KIND_LABEL: Record<NetworkKind, string> = {
  activate: 'Activate',
  suspend: 'Suspend',
  restore: 'Restore',
  deactivate: 'Deactivate',
  change_profile: 'Change profile',
  swap_sim: 'Swap SIM',
  update_e911: 'Update E911 address',
};

export const NETWORK_STATE_LABEL: Record<NetworkState, string> = {
  pending: 'Pending',
  confirmed: 'Confirmed',
  refused: 'Refused',
  exhausted: 'Exhausted',
  cancelled: 'Cancelled',
};

export type NetworkOwner = { orderId: string } | { productId: string };

/** The network commands of one order or one product, newest first. */
export function loadNetworkCommands(owner: NetworkOwner, signal: AbortSignal) {
  const query =
    'orderId' in owner ? `productOrder.id=${owner.orderId}` : `product.id=${owner.productId}`;
  return getAll<NetworkCommand>(`${NETWORK}?${query}`, signal);
}

/** Return an `exhausted` command to pending (202). It never sends: the worker does, on its
 * next tick, with the same key and request. */
export async function retryNetworkCommand(id: string): Promise<NetworkCommand> {
  const response = await request('POST', `${NETWORK}/${id}/retry`);
  return (await response.json()) as NetworkCommand;
}
