import type { Route } from './router';

// The sidebar as data (#414): sections follow the TMF Open API / ODA domains and the DB
// areas (docs/db/README.md, docs/decisions/frontend.md). Every navigable route sits in
// exactly one item (nav.test.ts); `tmf` is the item's title attribute.

export type RouteName = Route['name'];

export interface NavItem {
  label: string;
  href: string;
  /** The routes that mark this item active. */
  routes: RouteName[];
  /** The TMF API and resource behind the screen, or "internal". */
  tmf: string;
  admin?: boolean;
  /** A red "failed" badge; Sidebar binds it to its hook. */
  badge?: 'runs' | 'commands';
}

export interface NavSection {
  key: string;
  label: string;
  items: NavItem[];
}

export const NAV: NavSection[] = [
  {
    key: 'customers',
    label: 'Customers',
    items: [
      {
        label: 'Subscribers',
        href: '/subscribers',
        routes: ['subscribers', 'subscriber'],
        tmf: 'TMF666 billingAccount',
      },
      {
        label: 'Parties',
        href: '/parties',
        routes: ['parties'],
        tmf: 'TMF632 individual/organization, TMF629 customer',
      },
      {
        label: 'Financial accounts',
        href: '/financial-accounts',
        routes: ['financialAccounts'],
        tmf: 'TMF666 financialAccount',
      },
    ],
  },
  {
    key: 'products',
    label: 'Products',
    items: [
      {
        label: 'Product catalog',
        href: '/catalog',
        routes: ['catalog'],
        tmf: 'TMF620 offering, specification, category, catalog',
      },
      { label: 'Orders', href: '/orders', routes: ['orders'], tmf: 'TMF622 productOrder' },
      { label: 'Products', href: '/products', routes: ['products'], tmf: 'TMF637 product' },
    ],
  },
  {
    key: 'resources',
    label: 'Resources',
    items: [
      {
        label: 'Numbers',
        href: '/numbers',
        routes: ['numbers'],
        tmf: 'TMF639 logicalResource (MSISDN)',
      },
      { label: 'SIMs', href: '/sims', routes: ['sims'], tmf: 'TMF639 resource (IMSI, SIM batch)' },
      {
        label: 'Port requests',
        href: '/ports',
        routes: ['ports'],
        tmf: 'internal (number portability)',
      },
    ],
  },
  {
    key: 'billing',
    label: 'Billing',
    items: [
      {
        label: 'Billing runs',
        href: '/billing-runs',
        routes: ['billingRuns'],
        tmf: 'internal',
        badge: 'runs',
      },
      { label: 'Bills', href: '/bills', routes: ['bills'], tmf: 'TMF678 customerBill' },
      { label: 'Wholesale', href: '/wholesale', routes: ['wholesale'], tmf: 'internal' },
      {
        label: 'Payments',
        href: '/payments',
        routes: ['payments'],
        tmf: 'TMF676 payment, refund',
      },
      {
        label: 'Billing settings',
        href: '/billing-settings',
        routes: ['billingSettings'],
        tmf: 'TMF678 billingCycleSpecification, billFormat, billPresentationMedia',
      },
    ],
  },
  {
    key: 'prepaid',
    label: 'Prepaid',
    items: [
      {
        label: 'Balances',
        href: '/balances',
        routes: ['balances'],
        tmf: 'TMF654 bucket, accumulatedBalance, top-up, adjust, reserve, transfer',
      },
      {
        label: 'Prepaid commands',
        href: '/prepaid-commands',
        routes: ['prepaidCommands'],
        tmf: 'internal',
        badge: 'commands',
      },
      {
        label: 'Prepaid lifecycle',
        href: '/prepaid-lifecycle',
        routes: ['prepaidLifecycle'],
        tmf: 'internal',
      },
    ],
  },
  {
    key: 'reports',
    label: 'Reports',
    items: [{ label: 'Prepaid KPIs', href: '/reports', routes: ['reports'], tmf: 'internal' }],
  },
  {
    key: 'system',
    label: 'System',
    items: [
      {
        label: 'Integrations',
        href: '/integrations',
        routes: ['integrations'],
        tmf: 'TMF630 hub, event subscriptions',
      },
      { label: 'Users', href: '/users', routes: ['users'], tmf: 'internal', admin: true },
      {
        label: 'Logging',
        href: '/settings/logging',
        routes: ['logging'],
        tmf: 'internal',
        admin: true,
      },
    ],
  },
];

/** Routes that are reached without the sidebar: the session pages, the kit, not-found and
 * the subscriber card (under Subscribers). */
export const OFF_NAV: RouteName[] = ['signIn', 'password', 'uiKit', 'notFound', 'subscriber'];

/** The sections an operator sees: admin-only items only for an admin; empty sections go. */
export function visibleNav(isAdmin: boolean): NavSection[] {
  return NAV.map((section) => ({
    ...section,
    items: section.items.filter((item) => isAdmin || !item.admin),
  })).filter((section) => section.items.length > 0);
}
