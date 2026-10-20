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
  charges: { count: 4, amounts: [{ unit: 'USD', value: 2241.4 }] },
  error: null,
  isLatest: true,
};

const usd = (value: number) => ({ unit: 'USD', value });

/** GET /internal/v1/billingAccount/{id}/settlement of an account with nothing on bills. */
export const NO_SETTLEMENT = {
  outstanding: usd(0),
  unallocatedCredit: usd(0),
  overdue: { count: 0, amount: usd(0) },
  onHold: { count: 0, amount: usd(0) },
  paymentTermDays: 30,
};

/** The bills list the card loads (TMF678, one page of 100). */
export function billsUrl(accountId: string): string {
  return `GET /tmf-api/customerBillManagement/v5/customerBill?billingAccount.id=${accountId}&offset=0&limit=100`;
}

/** A TMF678 CustomerBill as the API sends it; dates are midnights in Europe/Moscow. */
export function billBody(
  id: string,
  billDate: string, // business date
  amountDue: number,
  remaining: number,
  state: string,
  over: Record<string, unknown> = {},
) {
  const midnight = (day: string) => {
    const d = new Date(`${day}T00:00:00Z`);
    d.setUTCHours(-3); // the previous day, 21:00Z = midnight MSK
    return d.toISOString().replace('.000Z', 'Z');
  };
  const next = new Date(`${billDate}T00:00:00Z`);
  next.setUTCMonth(next.getUTCMonth() + 1);
  const due = new Date(`${billDate}T00:00:00Z`);
  due.setUTCDate(due.getUTCDate() + 30);
  const day = (d: Date) => d.toISOString().slice(0, 10);
  return {
    id,
    href: `/tmf-api/customerBillManagement/v5/customerBill/${id}`,
    billNo: `BL-${id.replace(/-/g, '').slice(0, 8).toUpperCase()}`,
    billDate: midnight(billDate),
    billingPeriod: { startDateTime: midnight(billDate), endDateTime: midnight(day(next)) },
    paymentDueDate: midnight(day(due)),
    amountDue: usd(amountDue),
    remainingAmount: usd(remaining),
    appliedPayment: [],
    category: amountDue < 0 ? 'creditNote' : 'normal',
    runType: 'onCycle',
    state,
    '@type': 'CustomerBill',
    ...over,
  };
}

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
        amount: { unit: 'USD', value: 0 },
        '@type': 'AccountBalance',
      },
    ],
    billCycleDay: 1,
    currency: 'USD',
    '@type': 'BssBillingAccount',
    ...over,
  };
}
