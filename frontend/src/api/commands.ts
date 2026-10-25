import type { components as Internal } from './generated/internal';
import { getAll, getJson, getPage, request } from './client';

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

export type CommandState = PrepaidCommand['state'];
export const COMMAND_STATES: CommandState[] = ['pending', 'completed', 'failedTechnical'];
export const COMMAND_PAGE = 50;

/** One page of the journal, newest first as served; `total` is every matching row. */
export function loadCommandPage(
  filter: { state: CommandState | null; account: string | null },
  offset: number,
  signal: AbortSignal,
) {
  const query = [
    ...(filter.account !== null ? [`billingAccount.id=${filter.account}`] : []),
    ...(filter.state !== null ? [`state=${filter.state}`] : []),
    `offset=${offset}`,
    `limit=${COMMAND_PAGE}`,
  ];
  return getPage<PrepaidCommand>(`${COMMANDS}?${query.join('&')}`, signal);
}

/** The sidebar badge: how many commands wait for an operator (failedTechnical). */
export function countFailedCommands(signal: AbortSignal): Promise<number> {
  return getPage<PrepaidCommand>(`${COMMANDS}?state=failedTechnical&limit=1`, signal).then(
    (page) => page.total,
  );
}

/** Return a failedTechnical command to pending (202; no body). It never posts: the worker
 * attempts it on its next tick. */
export async function retryCommand(id: string): Promise<PrepaidCommand> {
  const response = await request('POST', `${COMMANDS}/${id}/retry`);
  return (await response.json()) as PrepaidCommand;
}
