import { describe, expect, it } from 'vitest';

// #314: no screen reads a whole growing table. getAll walks every page of a list, so each
// caller must read one owner's rows (an account, product, order, bill or number) or a
// small admin list. A new caller fails this test until it is listed here with its reason;
// a global list of a growing table pages on the server instead (ui/paging.tsx).

const SOURCES = import.meta.glob<string>(['./*.ts', '!./*.test.ts', '!./client.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
});

const ALLOWED: Record<string, string> = {
  // one owner's rows
  'balances.ts:loadAccountTotals': "one account's accumulated balance",
  'bills.ts:loadRates': "one bill's rates",
  'bss.ts:loadBillDemands': "one account's bill demands",
  'bss.ts:loadBills': "one account's bills",
  'bss.ts:loadPrepaidBalance': "one account's buckets",
  'bss.ts:loadRefunds': "one account's refunds",
  'cards.ts:loadCards': "one account's payment cards",
  'commands.ts:loadFailedCommands': "one account's failed prepaid commands",
  'financial.ts:loadFinancialChoices': "one party's financial accounts",
  'history.ts:loadBalanceActions': "one account's or bucket's balance actions",
  'network.ts:loadNetworkCommands': "one order's or product's network commands",
  'numbers.ts:loadNumberProducts': "one number's products",
  'orders.ts:findOrderCommand': "one order's prepaid commands",
  'orders.ts:loadCancellations': "one order's cancellations",
  'payments.ts:loadPayments': "one account's payments",
  'ports.ts:loadPins': "one product's transfer PINs",
  'transfer.ts:findReceiver': 'one exact number value',
  'transfer.ts:loadOwnNumbers': "one account's active products",
  // small admin lists: the catalog and settings, not one row per subscriber
  'billing.ts:loadSettings': 'billing cycle, format and media settings',
  'bills.ts:loadBillCycles': 'bill cycles: one per billing date',
  'bss.ts:loadAddOnOfferings': 'the catalog',
  'bss.ts:loadAttachedPrices': 'the catalog',
  'bss.ts:loadCatalog': 'the catalog',
  'bss.ts:loadCategories': 'the catalog',
  'bss.ts:loadSellableOfferings': 'the catalog',
  'catalogJobs.ts:loadCatalogChoices': 'the catalogs',
  'catalogJobs.ts:loadJobs': 'catalog import and export jobs',
  'classification.ts:loadCatalogList': 'the catalogs',
  'classification.ts:loadCategoryList': 'the catalog categories',
  'hubs.ts:loadSubscriptions': 'event subscriptions',
  'bss.ts:loadFinancialAccounts': 'financial accounts: group accounts, not one per subscriber',
  'bss.ts:loadPartyAccounts': 'party and settlement accounts: not one per subscriber',
  'ports.ts:loadPorts': 'the port request queue (number portability), filtered',
};

/** `file:function` of every getAll call in the API modules. */
function callers(): string[] {
  const found = new Set<string>();
  for (const [path, source] of Object.entries(SOURCES)) {
    const lines = source.split('\n');
    lines.forEach((line, index) => {
      if (!line.includes('getAll<')) return;
      for (let i = index; i >= 0; i--) {
        const match = /function (\w+)/.exec(lines[i] ?? '');
        if (match) {
          found.add(`${path.replace('./', '')}:${match[1]}`);
          return;
        }
      }
    });
  }
  return [...found].sort();
}

describe('getAll callers (#314)', () => {
  it('reads only owner-scoped or small lists in full', () => {
    expect(callers()).toEqual(Object.keys(ALLOWED).sort());
  });
});
