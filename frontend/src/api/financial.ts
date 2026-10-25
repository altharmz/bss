// Financial account writes (TMF666) and the lists the forms need: owners to choose from and
// the financial accounts a billing account may link to. Design: docs/next-slices-design.md
// section 16; rules in docs/decisions/accounts.md.

import { AMOUNT, getAll, jsonWithAmount, patchJson, postJson, request } from './client';
import { PATHS, type AccountRecord, type AccountState, type PartyKind } from './bss';
import { PARTY_PATH } from './parties';

export interface OwnerChoice {
  id: string;
  kind: PartyKind;
  name: string;
}

interface PartyBody {
  id?: string;
  givenName?: string;
  familyName?: string;
  name?: string;
}

/** Individuals and organizations: the choices for the owner of a financial account. */
export async function loadOwnerChoices(signal: AbortSignal): Promise<OwnerChoice[]> {
  const [people, organizations] = await Promise.all([
    getAll<PartyBody>(PARTY_PATH.Individual, signal),
    getAll<PartyBody>(PARTY_PATH.Organization, signal),
  ]);
  const name = (body: PartyBody) =>
    body.name ?? [body.givenName, body.familyName].filter(Boolean).join(' ');
  return [
    ...people.map((b) => ({ id: b.id ?? '', kind: 'Individual' as const, name: name(b) })),
    ...organizations.map((b) => ({ id: b.id ?? '', kind: 'Organization' as const, name: name(b) })),
  ].filter((choice) => choice.id !== '');
}

export interface FinancialChoice {
  id: string;
  name: string;
  state: AccountState;
}

/** The financial accounts of one owner: what a billing account of that party may link to. */
export async function loadFinancialChoices(
  partyId: string,
  signal: AbortSignal,
): Promise<FinancialChoice[]> {
  const rows = await getAll<{ id?: string; name?: string; state?: string }>(
    `${PATHS.financialAccounts}?relatedParty.id=${encodeURIComponent(partyId)}`,
    signal,
  );
  return rows.map((row) => ({
    id: row.id ?? '',
    name: row.name ?? '',
    state: (row.state ?? 'Active') as AccountState,
  }));
}

/** The three TMF666 account resources the Accounts screen writes. */
export type AccountKind = 'FinancialAccount' | 'PartyAccount' | 'SettlementAccount';

export const ACCOUNT_PATH: Record<AccountKind, string> = {
  FinancialAccount: PATHS.financialAccounts,
  PartyAccount: PATHS.partyAccounts,
  SettlementAccount: PATHS.settlementAccounts,
};

export interface FinancialForm {
  name: string;
  owner: OwnerChoice | null;
  accountType: string;
  description: string;
  creditLimit: string | null; // canonical decimal string, or none
  currency: string;
  state: AccountState;
  /** party and settlement accounts: the financial account they link to (same owner) */
  financialId?: string | null;
}

export function formOf(
  account: AccountRecord & { financial?: { id: string } | null },
  owner: OwnerChoice | null,
  defaultCurrency: string,
): FinancialForm {
  return {
    name: account.name,
    owner,
    accountType: account.accountType ?? '',
    description: account.description ?? '',
    creditLimit: account.creditLimit?.value ?? null,
    currency: account.creditLimit?.unit ?? defaultCurrency,
    state: account.state,
    financialId: account.financial?.id ?? null,
  };
}

const financialRef = (id: string) => ({
  id,
  '@type': 'FinancialAccountRef',
  '@referredType': 'FinancialAccount',
});

const ownerEntry = (owner: OwnerChoice) => ({
  role: 'owner',
  partyOrPartyRole: { id: owner.id, '@type': 'PartyRef', '@referredType': owner.kind },
  '@type': 'RelatedPartyRefOrPartyRoleRef',
});

/** The POST body (JSON text: the credit limit is an exact decimal token). */
export function createBody(form: FinancialForm, kind: AccountKind = 'FinancialAccount'): string {
  const body: Record<string, unknown> = {
    '@type': kind,
    name: form.name.trim(),
    relatedParty: form.owner ? [ownerEntry(form.owner)] : [],
    state: form.state,
  };
  if (form.accountType.trim()) body.accountType = form.accountType.trim();
  if (form.description.trim()) body.description = form.description.trim();
  if (form.financialId) body.financialAccount = financialRef(form.financialId);
  if (form.creditLimit !== null) body.creditLimit = { unit: form.currency, value: AMOUNT };
  return form.creditLimit === null ? JSON.stringify(body) : jsonWithAmount(body, form.creditLimit);
}

/** The merge patch of only the changed fields; null = nothing changed. A cleared optional
 * field is sent as null. The owner is sent with the other related parties kept as loaded. */
export function patchBody(
  account: AccountRecord,
  before: FinancialForm,
  after: FinancialForm,
): string | null {
  const body: Record<string, unknown> = {};
  if (after.name.trim() !== before.name) body.name = after.name.trim();
  if (after.accountType.trim() !== before.accountType)
    body.accountType = after.accountType.trim() || null;
  if (after.description.trim() !== before.description)
    body.description = after.description.trim() || null;
  if (after.state !== before.state) body.state = after.state;
  if (after.owner && before.owner?.id !== after.owner.id)
    body.relatedParty = [ownerEntry(after.owner), ...account.otherParties];
  if ((after.financialId ?? null) !== (before.financialId ?? null))
    body.financialAccount = after.financialId ? financialRef(after.financialId) : null;
  const limitChanged =
    after.creditLimit !== before.creditLimit || after.currency !== before.currency;
  if (limitChanged) {
    body.creditLimit = after.creditLimit === null ? null : { unit: after.currency, value: AMOUNT };
  }
  if (Object.keys(body).length === 0) return null;
  return limitChanged && after.creditLimit !== null
    ? jsonWithAmount(body, after.creditLimit)
    : JSON.stringify(body);
}

export async function createAccount(kind: AccountKind, body: string): Promise<void> {
  await postJson(ACCOUNT_PATH[kind], body);
}

export async function patchAccount(kind: AccountKind, id: string, body: string): Promise<void> {
  await patchJson(`${ACCOUNT_PATH[kind]}/${id}`, body);
}

export async function deleteAccount(kind: AccountKind, id: string): Promise<void> {
  await request('DELETE', `${ACCOUNT_PATH[kind]}/${id}`);
}

export async function deleteBillingAccount(id: string): Promise<void> {
  await request('DELETE', `${PATHS.accounts}/${id}`);
}
