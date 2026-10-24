import type { components as Internal } from './generated/internal';
import { getAll, getJson, request } from './client';

// Prepaid commands (internal API, docs/decisions/prepaid.md P3i): the worker's queue behind a
// prepaid order, renewal or transfer. A prepaid order's id is its command's id.

const COMMANDS = '/internal/v1/prepaid/command';

export type PrepaidCommand = Internal['schemas']['PrepaidCommand'];

/** An order the API accepted without finishing it (`inProgress`). */
export interface PendingOrder {
  id: string;
  label: string;
}

export function loadCommand(id: string, signal: AbortSignal): Promise<PrepaidCommand> {
  return getJson<PrepaidCommand>(`${COMMANDS}/${id}`, signal);
}

/** Commands the worker could not finish for a technical reason; they wait for an automatic
 * retry or an operator's Retry. A funding shortfall is not one of these (it stays pending). */
export function loadFailedCommands(accountId: string, signal: AbortSignal) {
  return getAll<PrepaidCommand>(
    `${COMMANDS}?billingAccount.id=${accountId}&state=failedTechnical`,
    signal,
  );
}

/** Return a failedTechnical command to pending (202; no body). It never posts: the worker
 * attempts it on its next tick. */
export async function retryCommand(id: string): Promise<PrepaidCommand> {
  const response = await request('POST', `${COMMANDS}/${id}/retry`);
  return (await response.json()) as PrepaidCommand;
}
