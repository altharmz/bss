// Parties (TMF632 individual and organization) and customers (TMF629) as the Parties
// screen shows them: reachable even without a billing account. One load holds the three
// lists and what refers to them (billing accounts through customers, party/financial/
// settlement accounts through their owner), so the delete rules are visible in the data.
// Design: docs/ui.md.

import { accountPinHeader, getJson, getPage, patchJson, postJson, request } from './client';
import { PATHS, type PartyKind } from './bss';
import { businessDate, zonedDateTime } from '../format/dates';
import {
  contactFields,
  contactMedia,
  type ContactFields,
  type ContactMediumBody,
} from './partyDocuments';

const PARTIES = '/tmf-api/partyManagement/v5';
export const PARTY_PATH: Record<PartyKind, string> = {
  Individual: `${PARTIES}/individual`,
  Organization: `${PARTIES}/organization`,
};

interface PartyBody {
  id?: string;
  givenName?: string;
  familyName?: string;
  name?: string;
  status?: string;
}
interface CustomerBody {
  id?: string;
  name?: string;
  status?: string;
  engagedParty?: { id?: string; '@referredType'?: string };
}

function need<T>(value: T | undefined | null, what: string): T {
  if (value === undefined || value === null) throw new Error(`The server sent no ${what}`);
  return value;
}

export interface Party {
  id: string;
  kind: PartyKind;
  name: string;
  givenName: string;
  familyName: string;
  status: string;
}

export interface PartyCustomer {
  id: string;
  name: string;
  status: string;
  partyId: string;
  partyKind: PartyKind;
  partyName: string;
  /** its billing accounts */
  accounts: { id: string; name: string }[];
}

/** A row of the Parties list: the party with what the server computed for the page. */
export interface PartyRow {
  id: string;
  kind: PartyKind;
  name: string;
  status: string;
  customers: { id: string; name: string }[];
  /** the billing accounts of those customers */
  accounts: { id: string; name: string; customerId: string }[];
  /** it owns a financial, party or settlement account */
  ownsAccounts: boolean;
}

export function toParty(kind: PartyKind, body: PartyBody): Party {
  const givenName = body.givenName ?? '';
  const familyName = body.familyName ?? '';
  return {
    id: need(body.id, `${kind} id`),
    kind,
    name:
      kind === 'Individual'
        ? [givenName, familyName].filter(Boolean).join(' ')
        : need(body.name, 'organization name'),
    givenName,
    familyName,
    status: body.status ?? '',
  };
}

export const PARTY_PAGE = 50;
const PARTY_SEARCH = '/internal/v1/party';
const CUSTOMER_SEARCH = '/internal/v1/partyCustomer';

interface PartyRowBody {
  id: string;
  kind: PartyKind;
  name: string;
  status: string | null;
  customers: { id: string; name: string }[];
  billingAccounts: { id: string; name: string; customerId: string }[];
  ownsAccounts: boolean;
}

interface CustomerRowBody {
  id: string;
  name: string;
  status: string;
  party: { id: string; kind: PartyKind; name: string };
  billingAccounts: { id: string; name: string }[];
}

export interface PartyFilter {
  /** the start of a name, or of the id (any case) */
  q: string;
  status: string | null;
}

function searchQuery(filter: PartyFilter, offset: number, extra: Record<string, string> = {}) {
  const query = new URLSearchParams(extra);
  if (filter.q.trim()) query.set('q', filter.q.trim());
  if (filter.status) query.set('status', filter.status);
  query.set('offset', String(offset));
  query.set('limit', String(PARTY_PAGE));
  return query;
}

/** One page of individuals or organizations, searched by prefix on the server (#314). */
export async function loadPartyPage(
  kind: PartyKind,
  filter: PartyFilter,
  offset: number,
  signal: AbortSignal,
): Promise<{ items: PartyRow[]; more: boolean; offset: number }> {
  const page = await getPage<PartyRowBody>(
    `${PARTY_SEARCH}?${searchQuery(filter, offset, { kind })}`,
    signal,
  );
  const items = page.items.map((row) => ({
    id: row.id,
    kind: row.kind,
    name: row.name,
    status: row.status ?? '',
    customers: row.customers,
    accounts: row.billingAccounts,
    ownsAccounts: row.ownsAccounts,
  }));
  return { items, more: page.more, offset };
}

/** One page of customers, searched by prefix on the server (#314). */
export async function loadCustomerPage(
  filter: PartyFilter,
  offset: number,
  signal: AbortSignal,
): Promise<{ items: PartyCustomer[]; more: boolean; offset: number }> {
  const page = await getPage<CustomerRowBody>(
    `${CUSTOMER_SEARCH}?${searchQuery(filter, offset)}`,
    signal,
  );
  const items = page.items.map((row) => ({
    id: row.id,
    name: row.name,
    status: row.status,
    partyId: row.party.id,
    partyKind: row.party.kind,
    partyName: row.party.name,
    accounts: row.billingAccounts,
  }));
  return { items, more: page.more, offset };
}

/** What the row shows against deleting a party; null = nothing found. */
export function partyBlock(party: PartyRow): string | null {
  const customers = party.customers.length;
  if (customers > 0) return `It has ${customers} customer${customers === 1 ? '' : 's'}`;
  if (party.ownsAccounts) return 'It owns accounts';
  return null;
}

// --- one record (GET) and its writes ---

export const PARTY_STATUSES: Record<PartyKind, string[]> = {
  Individual: ['initialized', 'validated', 'deceased'],
  Organization: ['initialized', 'validated', 'closed'],
};

export interface PartyDetail extends ContactFields {
  id: string;
  kind: PartyKind;
  givenName: string;
  familyName: string;
  organizationName: string;
  status: string;
  birthDate: string;
  identificationType: string;
  identificationId: string;
  externalId: string;
  externalType: string;
}

interface IdentificationBody {
  '@type'?: string;
  identificationType?: string;
  identificationId?: string;
}
interface ExternalBody {
  '@type'?: string;
  externalIdentifierType?: string;
  id?: string;
}

export async function loadPartyDetail(
  kind: PartyKind,
  id: string,
  timeZone: string,
  signal: AbortSignal,
): Promise<PartyDetail> {
  const body = await getJson<
    PartyBody & {
      birthDate?: string;
      contactMedium?: ContactMediumBody[];
      individualIdentification?: IdentificationBody[];
      organizationIdentification?: IdentificationBody[];
      externalReference?: ExternalBody[];
    }
  >(`${PARTY_PATH[kind]}/${id}`, signal);
  const identification = (body.individualIdentification ?? body.organizationIdentification)?.[0];
  const external = body.externalReference?.[0];
  return {
    id,
    kind,
    givenName: body.givenName ?? '',
    familyName: body.familyName ?? '',
    organizationName: kind === 'Organization' ? need(body.name, 'organization name') : '',
    status: body.status ?? '',
    birthDate: body.birthDate ? businessDate(body.birthDate, timeZone) : '',
    ...contactFields(body.contactMedium),
    identificationType: identification?.identificationType ?? '',
    identificationId: identification?.identificationId ?? '',
    externalId: external?.id ?? '',
    externalType: external?.externalIdentifierType ?? '',
  };
}

export interface CustomerDetail {
  id: string;
  name: string;
  status: string;
  partyId: string;
  partyName: string;
  partyKind: PartyKind;
}

export async function loadCustomerDetail(id: string, signal: AbortSignal): Promise<CustomerDetail> {
  const body = await getJson<
    CustomerBody & { engagedParty?: CustomerBody['engagedParty'] & { name?: string } }
  >(`${PATHS.customers}/${id}`, signal);
  return {
    id: need(body.id, 'customer id'),
    name: need(body.name, 'customer name'),
    status: need(body.status, 'customer status'),
    partyId: need(body.engagedParty?.id, 'engagedParty id'),
    partyName: body.engagedParty?.name ?? '',
    partyKind: need(body.engagedParty?.['@referredType'], 'engagedParty type') as PartyKind,
  };
}

function identificationOf(form: PartyDetail): Record<string, string>[] {
  if (!form.identificationType.trim() && !form.identificationId.trim()) return [];
  return [
    {
      '@type':
        form.kind === 'Individual' ? 'IndividualIdentification' : 'OrganizationIdentification',
      identificationType: form.identificationType.trim(),
      identificationId: form.identificationId.trim(),
    },
  ];
}

function externalOf(form: PartyDetail): Record<string, string>[] {
  if (!form.externalType.trim() && !form.externalId.trim()) return [];
  return [
    {
      '@type': 'ExternalIdentifier',
      externalIdentifierType: form.externalType.trim(),
      id: form.externalId.trim(),
    },
  ];
}

/** A merge-patch of changed fields; documents are replaced whole and null clears birthDate. */
export function partyChanges(before: PartyDetail, after: PartyDetail, timeZone: string) {
  const changes: Record<string, unknown> = {};
  if (before.kind === 'Individual') {
    if (after.givenName.trim() !== before.givenName) changes.givenName = after.givenName.trim();
    if (after.familyName.trim() !== before.familyName) changes.familyName = after.familyName.trim();
    if (after.birthDate !== before.birthDate)
      changes.birthDate = after.birthDate
        ? zonedDateTime(after.birthDate, '00:00', timeZone)
        : null;
  } else if (after.organizationName.trim() !== before.organizationName) {
    changes.name = after.organizationName.trim();
  }
  if (after.status !== before.status) changes.status = after.status;
  if (JSON.stringify(contactMedia(after)) !== JSON.stringify(contactMedia(before)))
    changes.contactMedium = contactMedia(after);
  const identificationKey =
    before.kind === 'Individual' ? 'individualIdentification' : 'organizationIdentification';
  if (JSON.stringify(identificationOf(after)) !== JSON.stringify(identificationOf(before)))
    changes[identificationKey] = identificationOf(after);
  if (JSON.stringify(externalOf(after)) !== JSON.stringify(externalOf(before)))
    changes.externalReference = externalOf(after);
  return Object.keys(changes).length ? changes : null;
}

export interface NewParty extends ContactFields {
  givenName: string;
  familyName: string;
  organizationName: string;
  status: string;
  birthDate: string | null;
}

export async function createParty(kind: PartyKind, form: NewParty): Promise<void> {
  const media = contactMedia(form);
  await postJson(PARTY_PATH[kind], {
    '@type': kind,
    ...(kind === 'Individual'
      ? { givenName: form.givenName.trim(), familyName: form.familyName.trim() }
      : { name: form.organizationName.trim() }),
    status: form.status,
    ...(media.length ? { contactMedium: media } : {}),
    ...(kind === 'Individual' && form.birthDate ? { birthDate: form.birthDate } : {}),
  });
}

export async function createCustomer(
  party: Pick<Party, 'id' | 'kind'>,
  name: string,
): Promise<void> {
  await postJson(PATHS.customers, {
    '@type': 'Customer',
    name: name.trim(),
    status: 'Active',
    engagedParty: { id: party.id, '@type': 'PartyRef', '@referredType': party.kind },
  });
}

export function customerChanges(before: CustomerDetail, after: CustomerDetail) {
  const changes: Record<string, string> = {};
  if (after.name.trim() !== before.name) changes.name = after.name.trim();
  if (after.status !== before.status) changes.status = after.status;
  return Object.keys(changes).length ? changes : null;
}

/** A contact change of a prepaid customer's party needs the customer's account PIN
 * (`X-Account-Pin`, sent only when typed; docs/decisions/accounts.md). */
export async function patchParty(
  kind: PartyKind,
  id: string,
  changes: Record<string, unknown>,
  accountPin = '',
): Promise<void> {
  await patchJson(`${PARTY_PATH[kind]}/${id}`, changes, accountPinHeader(accountPin));
}

export async function patchCustomer(id: string, changes: Record<string, string>): Promise<void> {
  await patchJson(`${PATHS.customers}/${id}`, changes);
}

export async function deleteParty(kind: PartyKind, id: string): Promise<void> {
  await request('DELETE', `${PARTY_PATH[kind]}/${id}`);
}

export async function deleteCustomer(id: string): Promise<void> {
  await request('DELETE', `${PATHS.customers}/${id}`);
}
