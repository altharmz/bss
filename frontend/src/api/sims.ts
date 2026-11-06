import type { components as T639 } from './generated/tmf639';
import { getAll, getJson, postJson, request } from './client';
import { NUMBERS } from './numbers';

// SIM and eSIM stock (docs/decisions/sim.md): TMF639 lists and filters the SIMs, the bulk
// import and the eSIM generation are ours under /internal/v1/sim.
const SIM = '/internal/v1/sim';

export type SimType = 'physical' | 'esim';
export const SIM_STATES = ['in_stock', 'reserved', 'active', 'retired'] as const;
export type SimState = (typeof SIM_STATES)[number];

type ResourceBody = T639['schemas']['LogicalResource'];
export type Sim = ResourceBody & { id: string };

export interface SimBatch {
  name: string;
  simType: SimType;
  source: 'import' | 'generated';
  createdAt: string;
  total: number;
  counts: Record<SimState, number>;
}

export interface SimFilters {
  state: '' | SimState;
  type: '' | SimType;
  batch: string;
}

function characteristic(sim: ResourceBody, name: string): string | undefined {
  const found = (
    sim.resourceCharacteristic as { name?: string; value?: unknown }[] | undefined
  )?.find((c) => c.name === name);
  return typeof found?.value === 'string' ? found.value : undefined;
}

export const simIccid = (sim: ResourceBody) => characteristic(sim, 'iccid');
export const simState = (sim: ResourceBody) =>
  characteristic(sim, 'simState') as SimState | undefined;
export const simType = (sim: ResourceBody) => characteristic(sim, 'simType') as SimType | undefined;
/** `LPA:1$<smdp>$<matchingId>`: the server shows it only while the eSIM is reserved or
 * active, because the matching ID lets anyone install the profile. */
export const activationCode = (sim: ResourceBody) => characteristic(sim, 'activationCode');

/** The SIMs (IMSI resources), filtered by the TMF639 attributes the server defines. */
export async function loadSims(filters: SimFilters, signal: AbortSignal): Promise<Sim[]> {
  const query = new URLSearchParams({ 'resourceSpecification.id': 'imsi' });
  if (filters.state) query.set('simState', filters.state);
  if (filters.type) query.set('simType', filters.type);
  if (filters.batch) query.set('batchNumber', filters.batch);
  const rows = await getAll<ResourceBody>(`${NUMBERS}?${query}`, signal);
  return rows.map((row) => {
    if (!row.id) throw new Error('The API sent a SIM without an id');
    return { ...row, id: row.id };
  });
}

export function loadSim(id: string, signal: AbortSignal): Promise<Sim> {
  return getJson<ResourceBody>(`${NUMBERS}/${id}`, signal).then((row) => {
    if (!row.id) throw new Error('The API sent a SIM without an id');
    return { ...row, id: row.id };
  });
}

export function loadBatches(signal: AbortSignal): Promise<SimBatch[]> {
  return getJson<SimBatch[]>(`${SIM}/batch`, signal);
}

/** Stocks a CSV (`iccid,imsi[,smdpAddress,matchingId]`) as one batch: all or nothing. */
export async function importCsv(batch: string, type: SimType, csv: string): Promise<SimBatch> {
  const query = new URLSearchParams({ batch, simType: type });
  const response = await request('POST', `${SIM}/batch?${query}`, {
    body: csv,
    headers: { 'Content-Type': 'text/csv' },
  });
  return (await response.json()) as SimBatch;
}

export function generateEsims(batch: string, count: number): Promise<SimBatch> {
  return postJson<SimBatch>(`${SIM}/esim-generate`, { batch, count });
}
