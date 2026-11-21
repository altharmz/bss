import { describe, expect, it } from 'vitest';
import { NAV, OFF_NAV, visibleNav, type RouteName } from './nav';
import { parseRoute } from './router';

// The sidebar stays complete (#414): every navigable route has exactly one nav item, and
// every item opens the route it marks active. A new Route name fails to compile here until
// it is listed, so it cannot be left out of the menu unnoticed.

const ALL_ROUTES: Record<RouteName, true> = {
  uiKit: true,
  signIn: true,
  password: true,
  users: true,
  logging: true,
  numbers: true,
  sims: true,
  ports: true,
  subscribers: true,
  subscriber: true,
  catalog: true,
  orders: true,
  products: true,
  billingRuns: true,
  prepaidCommands: true,
  financialAccounts: true,
  parties: true,
  billingSettings: true,
  payments: true,
  bills: true,
  wholesale: true,
  balances: true,
  prepaidLifecycle: true,
  reports: true,
  integrations: true,
  notFound: true,
};

const items = NAV.flatMap((section) => section.items);

describe('sidebar navigation data', () => {
  it.each((Object.keys(ALL_ROUTES) as RouteName[]).filter((name) => !OFF_NAV.includes(name)))(
    'opens %s from exactly one nav item',
    (name) => {
      expect(items.filter((item) => parseRoute(item.href).name === name)).toHaveLength(1);
    },
  );

  it.each(items.map((item) => [item.label, item] as const))(
    '%s opens a route it marks active and names its TMF API',
    (_, item) => {
      const name = parseRoute(item.href).name;
      expect(OFF_NAV).not.toContain(name);
      expect(item.routes).toContain(name);
      expect(item.tmf).toMatch(/^(TMF\d{3}|internal)/);
    },
  );

  it('marks each route active in at most one item', () => {
    const marked = items.flatMap((item) => item.routes);
    expect(new Set(marked).size).toBe(marked.length);
  });

  it('has the TMF-domain sections in order', () => {
    expect(NAV.map((section) => section.label)).toEqual([
      'Customers',
      'Products',
      'Resources',
      'Billing',
      'Prepaid',
      'Reports',
      'System',
    ]);
  });

  it('shows Users and Logging only to an admin', () => {
    const labels = (admin: boolean) =>
      visibleNav(admin).flatMap((section) => section.items.map((item) => item.label));
    expect(labels(true)).toEqual(expect.arrayContaining(['Users', 'Logging']));
    expect(labels(false)).not.toContain('Users');
    expect(labels(false)).not.toContain('Logging');
    expect(labels(false)).toContain('Integrations');
  });
});
