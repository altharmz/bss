import type { components as T639 } from './generated/tmf639';
import type { components as T637 } from './generated/tmf637';
import { getAll, getJson, patchJson, postJson, request } from './client';
import { PATHS } from './bss';

export const NUMBERS = '/tmf-api/resourceInventory/v5/resource';
type NumberBody = T639['schemas']['LogicalResource'];
export type SubscriberNumber = NumberBody & { id: string; value: string };

function toNumber(body: NumberBody): SubscriberNumber {
  if (!body.id || !body.value) throw new Error('The API sent a number without id or value');
  return { ...body, id: body.id, value: body.value };
}

export async function requiresNumber(specificationId: string, signal: AbortSignal) {
  const spec = await getJson<{ resourceSpecification?: { id?: string }[] }>(
    `/tmf-api/productCatalogManagement/v5/productSpecification/${specificationId}`,
    signal,
  );
  return (spec.resourceSpecification ?? []).some((ref) => ref.id === 'msisdn');
}

export async function loadNumbers(signal: AbortSignal): Promise<SubscriberNumber[]> {
  return (await getAll<NumberBody>(NUMBERS, signal)).map(toNumber);
}

export function createNumber(value: string): Promise<SubscriberNumber> {
  return postJson<NumberBody>(NUMBERS, {
    '@type': 'LogicalResource',
    name: `MSISDN ${value}`,
    value,
    administrativeState: 'unlocked',
    resourceSpecification: { id: 'msisdn', '@type': 'ResourceSpecificationRef' },
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

export function numberProduct(id: string) {
  return {
    '@type': 'ProductRefOrValue',
    realizingResource: [{ id, '@type': 'ResourceRef', '@referredType': 'LogicalResource' }],
  };
}

export function loadNumberProducts(id: string, signal: AbortSignal) {
  return getAll<T637['schemas']['Product']>(
    `${PATHS.products}?realizingResource.id=${encodeURIComponent(id)}`,
    signal,
  );
}

export async function changeNumber(accountId: string, productId: string, numberId: string) {
  return postJson<{
    id: string;
    state: string;
    productOrderErrorMessage?: { code?: string; reason?: string }[];
  }>(PATHS.orders, {
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
  });
}
