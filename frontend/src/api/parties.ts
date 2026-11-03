// Parties (TMF632 individual and organization) and customers (TMF629) as the Parties
// screen shows them: reachable even without a billing account. One load holds the three
// lists and what refers to them (billing accounts through customers, party/financial/
// settlement accounts through their owner), so the delete rules are visible in the data.
// Design: docs/next-slices-design.md section 15.

import { getAll, getJson, patchJson, postJson, request } from './client';
import { PATHS, type PartyKind } from './bss';
import { businessDate, zonedDateTime } from '../format/dates';
import {
  contactFields,
  contactMedia,
  type ContactFields,
  type ContactMediumBody,
} from './partyDocuments';

const PARTIES = '/tmf-api/partyManagement/v5';
const ACCOUNTS = '/tmf-api/accountManagement/v5';

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
interface RelatedBody {
  relatedParty?: { role?: string; partyOrPartyRole?: { id?: string } }[];
}
interface AccountBody extends RelatedBody {
  id?: string;
  name?: string;
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
}

export interface AccountLink {
  id: string;
  name: string;
  customerId: string;
}

export interface Parties {
  individuals: Party[];
  organizations: Party[];
  customers: PartyCustomer[];
  /** billing accounts, matched to a customer by id */
  accounts: AccountLink[];
  /** party ids that own a financial, party or settlement account */
  accountOwners: Set<string>;
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

function toCustomer(body: CustomerBody): PartyCustomer {
  return {
    id: need(body.id, 'customer id'),
    name: need(body.name, 'customer name'),
    status: need(body.status, 'customer status'),
    partyId: need(body.engagedParty?.id, 'engagedParty id'),
    partyKind: need(body.engagedParty?.['@referredType'], 'engagedParty type') as PartyKind,
  };
}

const ownerOf = (body: RelatedBody) =>
  body.relatedParty?.find((p) => p.role === 'owner')?.partyOrPartyRole?.id;

export async function loadParties(signal: AbortSignal): Promise<Parties> {
  const owned = (kind: string) =>
    getAll<RelatedBody>(`${ACCOUNTS}/${kind}?fields=relatedParty`, signal);
  const [individuals, organizations, customers, accounts, financial, party, settlement] =
    await Promise.all([
      getAll<PartyBody>(PARTY_PATH.Individual, signal),
      getAll<PartyBody>(PARTY_PATH.Organization, signal),
      getAll<CustomerBody>(`${PATHS.customers}?fields=name,status,engagedParty`, signal),
      getAll<AccountBody>(`${PATHS.accounts}?fields=name,relatedParty`, signal),
      owned('financialAccount'),
      owned('partyAccount'),
      owned('settlementAccount'),
    ]);
  const accountOwners = new Set<string>();
  for (const body of [...financial, ...party, ...settlement]) {
    const id = ownerOf(body);
    if (id) accountOwners.add(id);
  }
  return {
    individuals: individuals.map((b) => toParty('Individual', b)),
    organizations: organizations.map((b) => toParty('Organization', b)),
    customers: customers.map(toCustomer),
    accounts: accounts.map((b) => ({
      id: need(b.id, 'account id'),
      name: b.name ?? '',
      customerId: need(
        b.relatedParty?.find((p) => p.role === 'customer')?.partyOrPartyRole?.id,
        'account customer',
      ),
    })),
    accountOwners,
  };
}

/** Customers engaging a party, and the billing accounts of those customers. */
export function customersOf(data: Parties, partyId: string): PartyCustomer[] {
  return data.customers.filter((c) => c.partyId === partyId);
}

export function accountsOf(data: Parties, customerIds: string[]): AccountLink[] {
  return data.accounts.filter((a) => customerIds.includes(a.customerId));
}

/** What the loaded data shows against deleting a party; null = nothing found. */
export function partyBlock(data: Parties, party: Party): string | null {
  const customers = customersOf(data, party.id).length;
  if (customers > 0) return `It has ${customers} customer${customers === 1 ? '' : 's'}`;
  if (data.accountOwners.has(party.id)) return 'It owns accounts';
  return null;
}

export function customerBlock(data: Parties, customer: PartyCustomer): string | null {
  const accounts = accountsOf(data, [customer.id]).length;
  return accounts > 0 ? `It has ${accounts} billing account${accounts === 1 ? '' : 's'}` : null;
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
  const c = toCustomer(body);
  return {
    id: c.id,
    name: c.name,
    status: c.status,
    partyId: c.partyId,
    partyName: body.engagedParty?.name ?? '',
    partyKind: c.partyKind,
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

export async function createCustomer(party: Party, name: string): Promise<void> {
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

export async function patchParty(
  kind: PartyKind,
  id: string,
  changes: Record<string, unknown>,
): Promise<void> {
  await patchJson(`${PARTY_PATH[kind]}/${id}`, changes);
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
