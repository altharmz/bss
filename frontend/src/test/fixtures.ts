// Response bodies shared by several component tests. Shared helpers live here, never in a
// *.test.tsx file: importing a test file registers its describe blocks again in the importer.

const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount';

/** A BillingRun (spec/internal) as GET /internal/v1/billingRun sends it: the scheduled
 * run of the fixtures' business date (CONTEXT, 2026-10-01), completed at 00:31:05 MSK. */
export const RUN = {
  id: 'b0000001-0000-4000-8000-000000000001',
  runDate: '2026-10-01',
  status: 'completed',
  failureKind: null,
  trigger: 'scheduled',
  attempt: 1,
  startedAt: '2026-09-30T21:30:00Z',
  finishedAt: '2026-09-30T21:31:05Z',
  heartbeatAt: '2026-09-30T21:31:05Z',
  accounts: { total: 3, ok: 3, failed: 0, blocked: 0 },
  charges: { count: 4, amounts: [{ unit: 'RUB', value: 2241.4 }] },
  error: null,
  isLatest: true,
};

export function accountBody(
  id: string,
  customer: { id: string; name: string },
  over: Record<string, unknown> = {},
) {
  return {
    id,
    href: `${ACCOUNTS}/${id}`,
    name: `${customer.name} main`,
    state: 'Active',
    relatedParty: [
      {
        role: 'customer',
        partyOrPartyRole: { id: customer.id, name: customer.name, '@type': 'PartyRoleRef' },
        '@type': 'RelatedPartyRefOrPartyRoleRef',
      },
    ],
    accountBalance: [
      {
        balanceType: 'currentBalance',
        amount: { unit: 'RUB', value: 0 },
        '@type': 'AccountBalance',
      },
    ],
    billCycleDay: 1,
    currency: 'RUB',
    '@type': 'BssBillingAccount',
    ...over,
  };
}
