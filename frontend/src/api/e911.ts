import { accountPinHeader, ApiError, postJson } from './client';
import { PATHS } from './bss';
import type { components as Internal } from './generated/internal';
import type { ServiceAddress } from './serviceAddress';

// The E911 address of a Wi-Fi calling line (docs/decisions/network.md#e911-address-issue-201,
// UI #384): TMF622/TMF637 `product.place`, one RelatedPlaceRefOrValue of role e911Address
// holding a US GeographicAddress. The server validates it (a 422 names the field) and decides
// which lines take one; the UI only offers it where the row's data says it applies.

export type E911Address = Internal['schemas']['E911Address'];

/** The form's fields; '' = not given (an optional field is then not sent). */
export interface E911Form {
  streetNr: string;
  streetName: string;
  streetSuffix: string;
  /** the first geographicSubAddress name (apartment, suite, floor) */
  line2: string;
  city: string;
  stateOrProvince: string;
  postcode: string;
}

export type E911Field = keyof E911Form;

export const EMPTY_E911: E911Form = {
  streetNr: '',
  streetName: '',
  streetSuffix: '',
  line2: '',
  city: '',
  stateOrProvince: '',
  postcode: '',
};

function subAddressName(address: E911Address): string {
  const first = (address.geographicSubAddress ?? [])[0] as { name?: unknown } | undefined;
  return typeof first?.name === 'string' ? first.name : '';
}

/** A stored address as the form shows it. */
export function formOf(address: E911Address): E911Form {
  return {
    streetNr: address.streetNr ?? '',
    streetName: address.streetName,
    streetSuffix: address.streetSuffix ?? '',
    line2: subAddressName(address),
    city: address.city,
    stateOrProvince: address.stateOrProvince,
    postcode: address.postcode,
  };
}

/** The service address as the server copies it when no address is sent: street1 becomes the
 * street name, street2 the sub-address (app/network/e911.py from_service_address). */
export function formOfServiceAddress(address: ServiceAddress): E911Form {
  return {
    ...EMPTY_E911,
    streetName: address.street1,
    line2: address.street2,
    city: address.city,
    stateOrProvince: address.stateOrProvince,
    postcode: address.postCode,
  };
}

/** The required fields are filled (the server checks the rest). */
export function e911Ready(form: E911Form): boolean {
  return (
    form.streetName.trim() !== '' &&
    form.city.trim() !== '' &&
    form.stateOrProvince !== '' &&
    form.postcode.trim() !== ''
  );
}

/** The `product.place` an order item sends: optional fields only when given. */
export function placeOf(form: E911Form) {
  const optional = (name: 'streetNr' | 'streetSuffix') =>
    form[name].trim() ? { [name]: form[name].trim() } : {};
  const line2 = form.line2.trim();
  return [
    {
      '@type': 'RelatedPlaceRefOrValue',
      role: 'e911Address',
      place: {
        '@type': 'GeographicAddress',
        ...optional('streetNr'),
        streetName: form.streetName.trim(),
        ...optional('streetSuffix'),
        city: form.city.trim(),
        stateOrProvince: form.stateOrProvince,
        postcode: form.postcode.trim(),
        country: 'US',
        ...(line2
          ? { geographicSubAddress: [{ '@type': 'GeographicSubAddress', name: line2 }] }
          : {}),
      },
    },
  ];
}

/** The address as display lines: street, sub-address, city line, country. */
export function e911Lines(address: E911Address): string[] {
  const street = [address.streetNr, address.streetName, address.streetSuffix]
    .filter(Boolean)
    .join(' ');
  const line2 = subAddressName(address);
  return [
    street,
    ...(line2 ? [line2] : []),
    `${address.city}, ${address.stateOrProvince} ${address.postcode}`,
    address.country,
  ];
}

/** One line: "742 Evergreen Terrace, Springfield, OR 97403". */
export function e911Text(address: E911Address): string {
  return e911Lines(address).slice(0, -1).join(', ');
}

/** Change the line's E911 address: a modify item whose only content is `product.place`,
 * with the account PIN of a protected customer (docs/decisions/network.md). */
export function changeE911(accountId: string, productId: string, form: E911Form, pin = '') {
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
          product: { '@type': 'ProductRefOrValue', id: productId, place: placeOf(form) },
        },
      ],
    },
    accountPinHeader(pin),
  );
}

const FIELD_OF: Record<string, E911Field> = {
  streetNr: 'streetNr',
  streetName: 'streetName',
  streetSuffix: 'streetSuffix',
  city: 'city',
  stateOrProvince: 'stateOrProvince',
  postcode: 'postcode',
};

/** A 422's problems mapped to the form's inputs. The API names each one by its path,
 * "productOrderItem.0.product.place.0.place.postcode: postcode must be a ZIP ..." (several
 * joined by "; "); a problem naming no input is left for the footer. */
export function e911Errors(error: unknown): {
  fields: Partial<Record<E911Field, string>>;
  rest: string[];
} {
  const fields: Partial<Record<E911Field, string>> = {};
  const rest: string[] = [];
  if (!(error instanceof ApiError) || error.status !== 422) return { fields, rest };
  for (const part of error.detail.split('; ')) {
    const match = /place\.0\.place\.(\w+): (.*)$/.exec(part);
    const field = match ? FIELD_OF[match[1] as string] : undefined;
    if (match && field !== undefined) fields[field] = match[2] as string;
    else if (part.trim()) rest.push(part);
  }
  return { fields, rest };
}
