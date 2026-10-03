// Response bodies shared by several component tests. Shared helpers live here, never in a
// *.test.tsx file: importing a test file registers its describe blocks again in the importer.

const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount';

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
