import { ApiError } from './client';

// The billing account's service address (docs/decisions/taxes.md, #207): the TMF666 contact
// of type serviceAddress with one US GeographicAddressContactMedium. The UI writes it with
// a merge patch of the account's whole `contact` array (merge-patch replaces an array): the
// serviceAddress entry is replaced or removed, every other contact goes back as loaded.

export const SERVICE_ADDRESS = 'serviceAddress';

export interface ServiceAddress {
  street1: string;
  street2: string; // '' = none (not sent)
  city: string;
  stateOrProvince: string;
  postCode: string;
}

export type AddressField = keyof ServiceAddress;

/** The US state and territory codes the API accepts (app/billing/engine_tax.py STATES). */
export const US_STATES = [
  'AK', 'AL', 'AR', 'AS', 'AZ', 'CA', 'CO', 'CT', 'DC', 'DE', 'FL', 'GA', 'GU', 'HI', 'IA',
  'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD', 'ME', 'MI', 'MN', 'MO', 'MP', 'MS', 'MT',
  'NC', 'ND', 'NE', 'NH', 'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'PR', 'RI', 'SC',
  'SD', 'TN', 'TX', 'UT', 'VA', 'VI', 'VT', 'WA', 'WI', 'WV', 'WY',
]; // prettier-ignore

type Contact = Record<string, unknown>;

const isServiceAddress = (contact: unknown): boolean =>
  typeof contact === 'object' &&
  contact !== null &&
  (contact as Contact).contactType === SERVICE_ADDRESS;

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

/** The service address in a loaded `contact` array (its first geographic medium), or null. */
export function serviceAddressOf(contacts: readonly unknown[]): ServiceAddress | null {
  const contact = contacts.find(isServiceAddress) as Contact | undefined;
  const media = Array.isArray(contact?.contactMedium) ? (contact.contactMedium as Contact[]) : [];
  const medium = media.find((m) => m?.['@type'] === 'GeographicAddressContactMedium');
  if (medium === undefined) return null;
  return {
    street1: text(medium.street1),
    street2: text(medium.street2),
    city: text(medium.city),
    stateOrProvince: text(medium.stateOrProvince),
    postCode: text(medium.postCode),
  };
}

function addressContact(address: ServiceAddress, before: Contact | undefined): Contact {
  const street2 = address.street2.trim();
  return {
    ...(before ?? { '@type': 'Contact' }),
    contactType: SERVICE_ADDRESS,
    contactMedium: [
      {
        '@type': 'GeographicAddressContactMedium',
        street1: address.street1.trim(),
        ...(street2 ? { street2 } : {}),
        city: address.city.trim(),
        stateOrProvince: address.stateOrProvince,
        postCode: address.postCode.trim(),
        country: 'US',
      },
    ],
  };
}

/** The whole `contact` array to send: the serviceAddress entry replaced in place (its other
 * attributes kept), added at the end when there was none, or removed when `address` is null;
 * every other contact exactly as loaded. */
export function contactsWith(
  contacts: readonly unknown[],
  address: ServiceAddress | null,
): unknown[] {
  const at = contacts.findIndex(isServiceAddress);
  if (address === null) return contacts.filter((_, i) => i !== at);
  if (at < 0) return [...contacts, addressContact(address, undefined)];
  return contacts.map((c, i) => (i === at ? addressContact(address, c as Contact) : c));
}

const FIELDS: AddressField[] = ['street1', 'street2', 'city', 'stateOrProvince', 'postCode'];

/** A 422's problems mapped to the form's inputs. The API names each problem as
 * "contact.0.contactMedium.0: postCode must be a ZIP ..." (several joined by "; "); a
 * problem naming no input (country, a missing medium) is left for the footer. */
export function addressErrors(error: unknown): {
  fields: Partial<Record<AddressField, string>>;
  rest: string[];
} {
  const fields: Partial<Record<AddressField, string>> = {};
  const rest: string[] = [];
  if (!(error instanceof ApiError) || error.status !== 422) return { fields, rest };
  for (const part of error.detail.split('; ')) {
    const match = /^contact[.\w]*: (\w+) (.*)$/.exec(part);
    const name = match?.[1] as AddressField | undefined;
    if (match && name !== undefined && FIELDS.includes(name)) {
      fields[name] = `${match[1]} ${match[2]}`;
    } else if (part.trim()) {
      rest.push(part);
    }
  }
  return { fields, rest };
}
