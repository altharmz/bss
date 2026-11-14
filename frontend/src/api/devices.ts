import { getJson, postJson } from './client';
import { PATHS } from './bss';

// The device (IMEI) of a prepaid line (docs/decisions/network.md#device-issue-194). TMF637
// shows the current device as product characteristics; the history and the check are ours
// under /internal/v1/device. Orders alone write it: an add item carries the `imei`
// characteristic, a device change is a modify item with only that characteristic.
const DEVICE = '/internal/v1/device';

export type Compatibility = 'compatible' | 'unknown';

export interface ProductDevice {
  id: string;
  product: { id: string };
  imei: string;
  compatibility: Compatibility;
  reasons: string[];
  checkedAt: string;
  startedAt: string;
  endedAt: string | null;
  recordedByItem: { id: string };
}

export interface DeviceCheck {
  imei: string;
  result: Compatibility | 'incompatible';
  reasons: string[];
}

/** Why the typed IMEI is not 15 digits with a correct Luhn check digit, or null. Only to
 * enable the buttons: the server checks it again (422 `invalidImei`). */
export function imeiProblem(value: string): string | null {
  if (!/^[0-9]{15}$/.test(value)) return 'An IMEI is 15 digits.';
  let sum = 0;
  for (let i = 0; i < 15; i++) {
    let digit = Number(value[14 - i]);
    if (i % 2 === 1) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
  }
  return sum % 10 === 0 ? null : 'The check digit (the last one) is wrong.';
}

/** The line's devices, newest first; the one without endedAt is the current device. */
export function loadDevices(productId: string, signal: AbortSignal): Promise<ProductDevice[]> {
  return getJson<ProductDevice[]>(
    `${DEVICE}?${new URLSearchParams({ 'product.id': productId })}`,
    signal,
  );
}

/** Ask the network whether a device works. Nothing is stored. */
export function checkDevice(imei: string, signal?: AbortSignal): Promise<DeviceCheck> {
  return getJson<DeviceCheck>(`${DEVICE}/check?${new URLSearchParams({ imei })}`, signal);
}

/** The `imei` product characteristic of an order item. */
export function imeiCharacteristic(imei: string) {
  return { '@type': 'StringCharacteristic', name: 'imei', valueType: 'string', value: imei };
}

type Characteristic = { name?: string; value?: unknown };

/** A characteristic's string value, from a TMF637 product or an order item's product. */
export function characteristicOf(product: unknown, name: string): string | undefined {
  const found = (
    product as { productCharacteristic?: Characteristic[] } | undefined
  )?.productCharacteristic?.find((c) => c.name === name);
  return typeof found?.value === 'string' ? found.value : undefined;
}

/** The device warning an accepted order echoed on its item: the network could not say
 * whether the device works (`unknown`), with its reasons. Null when there is none. */
export function deviceWarning(items: { product?: unknown }[] | undefined): string | null {
  for (const item of items ?? []) {
    if (characteristicOf(item.product, 'deviceCompatibility') === 'unknown') {
      const reasons = characteristicOf(item.product, 'deviceReasons');
      return `The network could not confirm the device works${reasons ? `: ${reasons}` : ''}.`;
    }
  }
  return null;
}

/** A device change: a modify item whose only content is the `imei` characteristic. */
export function changeDevice(accountId: string, productId: string, imei: string) {
  return postJson<{
    id: string;
    state: string;
    productOrderItem?: { product?: unknown }[];
    productOrderErrorMessage?: { code?: string; reason?: string }[];
  }>(PATHS.orders, {
    '@type': 'ProductOrder',
    billingAccount: { id: accountId, '@type': 'BillingAccountRef' },
    productOrderItem: [
      {
        '@type': 'ProductOrderItem',
        id: '1',
        action: 'modify',
        product: {
          '@type': 'ProductRefOrValue',
          id: productId,
          productCharacteristic: [imeiCharacteristic(imei)],
        },
      },
    ],
  });
}
