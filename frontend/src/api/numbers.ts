import type { components as T639 } from './generated/tmf639';
import type { components as T637 } from './generated/tmf637';
import { accountPinHeader, getAll, getJson, getPage, patchJson, postJson, request } from './client';
import { PATHS } from './bss';

export const NUMBERS = '/tmf-api/resourceInventory/v5/resource';
type NumberBody = T639['schemas']['LogicalResource'];
export type SubscriberNumber = NumberBody & { id: string; value: string };

function toNumber(body: NumberBody): SubscriberNumber {
  if (!body.id || !body.value) throw new Error('The API sent a number without id or value');
  return { ...body, id: body.id, value: body.value };
}

export type ResourceKind = 'msisdn' | 'imsi';

export function kindOf(n: { resourceSpecification?: { id?: string } }): ResourceKind {
  return n.resourceSpecification?.id === 'imsi' ? 'imsi' : 'msisdn';
}

export function iccidOf(n: NumberBody): string | undefined {
  return (n.resourceCharacteristic as { name?: string; value?: unknown }[] | undefined)?.find(
    (c) => c.name === 'iccid',
  )?.value as string | undefined;
}

export async function requirements(specificationId: string, signal: AbortSignal) {
  const spec = await getJson<{ resourceSpecification?: { id?: string }[] }>(
    `/tmf-api/productCatalogManagement/v5/productSpecification/${specificationId}`,
    signal,
  );
  const ids = (spec.resourceSpecification ?? []).map((ref) => ref.id);
  return { msisdn: ids.includes('msisdn'), imsi: ids.includes('imsi') };
}

export async function requiresNumber(specificationId: string, signal: AbortSignal) {
  return (await requirements(specificationId, signal)).msisdn;
}

export const NUMBER_PAGE = 50;

/** The digits of a typed number: E.164 is stored without `+`, spaces or dashes. */
export function numberDigits(text: string): string {
  return text.replace(/\D/g, '');
}

/**
 * One page of the inventory, oldest first (#314). `value` is an exact server filter (the
 * API has no partial match), `kind` the resource specification.
 */
export async function loadNumberPage(
  filter: { value: string | null; kind: ResourceKind | null },
  offset: number,
  signal: AbortSignal,
): Promise<{ items: SubscriberNumber[]; more: boolean; offset: number }> {
  const query = new URLSearchParams();
  if (filter.value) query.set('value', filter.value);
  if (filter.kind) query.set('resourceSpecification.id', filter.kind);
  query.set('offset', String(offset));
  query.set('limit', String(NUMBER_PAGE));
  const page = await getPage<NumberBody>(`${NUMBERS}?${query}`, signal);
  return { items: page.items.map(toNumber), more: page.more, offset };
}

/**
 * Numbers a picker offers: the first page of unlocked, available numbers of `kind`, and
 * the typed number when it is one of them (exact value), wherever it is in the inventory.
 */
export async function loadAvailableNumbers(
  kind: ResourceKind,
  value: string,
  signal: AbortSignal,
): Promise<SubscriberNumber[]> {
  const available = `resourceSpecification.id=${kind}&administrativeState=unlocked&allocationStatus=available`;
  const [first, typed] = await Promise.all([
    getPage<NumberBody>(`${NUMBERS}?${available}&offset=0&limit=${NUMBER_PAGE}`, signal),
    value
      ? getPage<NumberBody>(
          `${NUMBERS}?${available}&value=${encodeURIComponent(value)}&limit=1`,
          signal,
        )
      : Promise.resolve({ items: [], more: false }),
  ]);
  const numbers = [...typed.items, ...first.items].map(toNumber);
  return numbers.filter((n, i) => numbers.findIndex((m) => m.id === n.id) === i);
}

/** These numbers, one read each (a product's realizing resources). */
export async function loadNumbersById(
  ids: string[],
  signal: AbortSignal,
): Promise<SubscriberNumber[]> {
  const bodies = await Promise.all(
    [...new Set(ids)].map((id) => getJson<NumberBody>(`${NUMBERS}/${id}`, signal)),
  );
  return bodies.map(toNumber);
}

export function createNumber(
  value: string,
  kind: ResourceKind = 'msisdn',
  iccid = '',
): Promise<SubscriberNumber> {
  return postJson<NumberBody>(NUMBERS, {
    '@type': 'LogicalResource',
    name: `${kind === 'imsi' ? 'IMSI' : 'MSISDN'} ${value}`,
    value,
    administrativeState: 'unlocked',
    resourceSpecification: { id: kind, '@type': 'ResourceSpecificationRef' },
    ...(kind === 'imsi' && iccid
      ? {
          resourceCharacteristic: [
            { '@type': 'StringCharacteristic', name: 'iccid', valueType: 'string', value: iccid },
          ],
        }
      : {}),
  }).then(toNumber);
}

export function lockNumber(id: string, locked: boolean): Promise<SubscriberNumber> {
  return patchJson<NumberBody>(`${NUMBERS}/${id}`, {
    administrativeState: locked ? 'locked' : 'unlocked',
  }).then(toNumber);
}

export function loadNumber(id: string, signal: AbortSignal): Promise<SubscriberNumber> {
  return getJson<NumberBody>(`${NUMBERS}/${id}`, signal).then(toNumber);
}

/** Deletes a number that was never assigned; the server refuses one with history (409). */
export async function deleteNumber(id: string): Promise<void> {
  await request('DELETE', `${NUMBERS}/${id}`);
}

/** One reference per resource kind: the complete set the product realizes. */
export function numberProduct(ids: string | string[]) {
  return {
    '@type': 'ProductRefOrValue',
    realizingResource: [ids].flat().map((id) => ({
      id,
      '@type': 'ResourceRef',
      '@referredType': 'LogicalResource',
    })),
  };
}

export function loadNumberProducts(id: string, signal: AbortSignal) {
  return getAll<T637['schemas']['Product']>(
    `${PATHS.products}?realizingResource.id=${encodeURIComponent(id)}`,
    signal,
  );
}

/** A number change or SIM swap order. On a prepaid line the server asks for the customer's
 * account PIN (`X-Account-Pin`, sent only when typed) and rejects the order without it. */
export async function changeNumber(
  accountId: string,
  productId: string,
  numberId: string | string[],
  accountPin = '',
) {
  return postJson<{
    id: string;
    state: string;
    productOrderErrorMessage?: { code?: string; reason?: string }[];
  }>(
    PATHS.orders,
    {
      '@type': 'ProductOrder',
      billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
      productOrderItem: [
        {
          '@type': 'ProductOrderItem',
          id: '1',
          action: 'modify',
          product: { ...numberProduct(numberId), id: productId },
        },
      ],
    },
    accountPinHeader(accountPin),
  );
}
