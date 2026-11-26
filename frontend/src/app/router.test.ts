import { describe, expect, it } from 'vitest';
import { hrefOf, parseRoute, SETTINGS_TABS, type Route, type SettingsTab } from './router';

const ID = '1f3a9c2b-0000-4000-8000-000000000001';
type Linkable = Exclude<Route, { name: 'notFound' }>;

// Every screen's link (hrefOf) and the path it is known by: the sidebar, breadcrumbs and
// in-page links build hrefs with hrefOf, so each must open the same route again.
const LINKS: [Linkable, string][] = [
  [{ name: 'uiKit' }, '/ui-kit'],
  [{ name: 'signIn' }, '/sign-in'],
  [{ name: 'password' }, '/change-password'],
  [{ name: 'users' }, '/users'],
  [{ name: 'integrations' }, '/integrations'],
  [{ name: 'logging' }, '/settings/logging'],
  [{ name: 'numbers' }, '/numbers'],
  [{ name: 'sims' }, '/sims'],
  [{ name: 'ports' }, '/ports'],
  [{ name: 'subscribers' }, '/billing-accounts'],
  [{ name: 'subscriber', accountId: ID }, `/billing-accounts/${ID}`],
  [{ name: 'catalog' }, '/catalog'],
  [{ name: 'orders' }, '/orders'],
  [{ name: 'products' }, '/products'],
  [{ name: 'product', productId: ID }, `/products/${ID}`],
  [{ name: 'billingRuns' }, '/billing-runs'],
  [{ name: 'prepaidCommands' }, '/prepaid-commands'],
  [{ name: 'financialAccounts' }, '/financial-accounts'],
  [{ name: 'careSignups' }, '/signups'],
  [{ name: 'parties' }, '/parties'],
  [{ name: 'customers' }, '/customers'],
  ...(Object.keys(SETTINGS_TABS) as SettingsTab[]).map((tab): [Linkable, string] => [
    { name: 'settings', tab },
    `/settings/${SETTINGS_TABS[tab].path}`,
  ]),
  [{ name: 'payments' }, '/payments'],
  [{ name: 'dunning' }, '/dunning'],
  [{ name: 'bills' }, '/bills'],
  [{ name: 'wholesale' }, '/wholesale'],
  [{ name: 'wholesale', invoiceId: ID }, `/wholesale/${ID}`],
  [{ name: 'balances' }, '/balances'],
  [{ name: 'reports' }, '/reports'],
];

describe('hrefOf', () => {
  it.each(LINKS)('links %j to %s, which opens it again', (route, href) => {
    expect(hrefOf(route)).toBe(href);
    expect(parseRoute(href)).toEqual(route);
  });

  it('covers every route but notFound', () => {
    // a Record over the union: a new route fails the type check here until it is listed
    const every: Record<Linkable['name'], true> = {
      uiKit: true,
      signIn: true,
      password: true,
      users: true,
      integrations: true,
      logging: true,
      numbers: true,
      sims: true,
      ports: true,
      subscribers: true,
      subscriber: true,
      catalog: true,
      orders: true,
      products: true,
      product: true,
      billingRuns: true,
      prepaidCommands: true,
      financialAccounts: true,
      careSignups: true,
      parties: true,
      customers: true,
      settings: true,
      payments: true,
      dunning: true,
      bills: true,
      wholesale: true,
      balances: true,
      reports: true,
    };
    expect(new Set(LINKS.map(([route]) => route.name))).toEqual(new Set(Object.keys(every)));
  });
});
