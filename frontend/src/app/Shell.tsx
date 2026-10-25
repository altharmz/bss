import { Fragment, type ReactNode } from 'react';
import { loadRuns } from '../api/bss';
import { countFailedCommands } from '../api/commands';
import { countOrders } from '../api/orders';
import { getPage } from '../api/client';
import { useResource } from '../api/useResource';
import { formatInt } from '../format/money';
import { utcLabel } from '../format/dates';
import { useCrumbs, useNavVersion, type SystemContext } from './context';
import { Link, useRouter, type Route } from './router';
import './shell.css';
import { SignOut, useSession } from './session';

// The frame around every screen: sidebar (brand, navigation, operator) and top bar
// (breadcrumbs, business date, time zone). Billing runs shows the number of runs, or the
// red "failed" badge instead when the latest run failed (README "Global Layout").

const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount';
const OFFERINGS = '/tmf-api/productCatalogManagement/v5/productOffering';

function useCount(path: string): string {
  const total = useResource(path, (signal) =>
    getPage(`${path}?limit=1&fields=id`, signal).then((page) => page.total),
  );
  return total.status === 'ok' ? formatInt(total.data) : '';
}

/** The number of runs and whether the latest failed: one run, newest first. Reloaded when
 * the Billing runs screen sees its latest run change (useRefreshNav). */
function useRunsNav(): { count: string; failed: boolean } {
  const version = useNavVersion();
  const runs = useResource(`nav:runs:${version}`, (signal) => loadRuns(signal, 1));
  if (runs.status !== 'ok') return { count: '', failed: false };
  return {
    count: formatInt(runs.data.total),
    failed: runs.data.items[0]?.status === 'failed',
  };
}

/** Whether any prepaid command failed technically. The Prepaid commands screen asks for a
 * reload (useRefreshNav) after a retry. */
function useCommandsNav(): boolean {
  const version = useNavVersion();
  const failed = useResource(`nav:commands:${version}`, countFailedCommands);
  return failed.status === 'ok' && failed.data > 0;
}

function useOrderCount(): string {
  const total = useResource('nav:orders', countOrders);
  return total.status === 'ok' ? formatInt(total.data) : '';
}

interface NavItem {
  label: string;
  href: string;
  count: string;
  failed?: boolean;
  active: (route: Route) => boolean;
}

export function Sidebar({ context }: { context: SystemContext }) {
  const session = useSession();
  const { route } = useRouter();
  const accounts = useCount(ACCOUNTS);
  const orders = useOrderCount();
  const offerings = useCount(OFFERINGS);
  const runs = useRunsNav();
  const commandsFailed = useCommandsNav();
  // Sections follow the TMF areas: party/customer, product, billing
  const sections: { label: string; items: NavItem[] }[] = [
    {
      label: 'Customers',
      items: [
        {
          label: 'Subscribers',
          href: '/subscribers',
          count: accounts,
          active: (r) => r.name === 'subscribers' || r.name === 'subscriber',
        },
        { label: 'Parties', href: '/parties', count: '', active: (r) => r.name === 'parties' },
      ],
    },
    {
      label: 'Products',
      items: [
        {
          label: 'Product catalog',
          href: '/catalog',
          count: offerings,
          active: (r) => r.name === 'catalog',
        },
        { label: 'Orders', href: '/orders', count: orders, active: (r) => r.name === 'orders' },
        { label: 'Numbers', href: '/numbers', count: '', active: (r) => r.name === 'numbers' },
      ],
    },
    {
      label: 'Billing',
      items: [
        {
          label: 'Billing runs',
          href: '/billing-runs',
          count: runs.count,
          failed: runs.failed,
          active: (r) => r.name === 'billingRuns',
        },
        {
          label: 'Prepaid commands',
          href: '/prepaid-commands',
          count: '',
          failed: commandsFailed,
          active: (r) => r.name === 'prepaidCommands',
        },
        {
          label: 'Accounts',
          href: '/financial-accounts',
          count: '',
          active: (r) => r.name === 'financialAccounts',
        },
        {
          label: 'Billing settings',
          href: '/billing-settings',
          count: '',
          active: (r) => r.name === 'billingSettings',
        },
      ],
    },
  ];
  return (
    <aside className="bss-sidebar">
      <div className="bss-brand">
        <span className="mark">B</span>
        <span className="name">BSS Console</span>
        <span className="env">{context.environment.toUpperCase()}</span>
      </div>
      <nav className="bss-nav" aria-label="Main">
        {sections.map((section) => (
          <Fragment key={section.label}>
            <div className="section" data-ui={`ui.shell.section.${section.label.toLowerCase()}`}>
              {section.label}
            </div>
            {section.items.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={item.active(route) ? 'page' : undefined}
              >
                {item.label}
                {item.failed ? (
                  <span className="badge">failed</span>
                ) : (
                  <span className="count">{item.count}</span>
                )}
              </Link>
            ))}
          </Fragment>
        ))}
      </nav>
      <div className="bss-operator">
        <details data-ui="ui.session.menu">
          <summary>
            <span className="who">{session?.username}</span> · {session?.role}
          </summary>
          <nav aria-label="User menu">
            <Link href="/change-password">Change password</Link>
            {session?.role === 'admin' && <Link href="/users">Users</Link>}
            <SignOut />
          </nav>
        </details>
      </div>
    </aside>
  );
}

export function TopBar({ context }: { context: SystemContext | null }) {
  const crumbs = useCrumbs();
  return (
    <header className="bss-topbar">
      <nav className="bss-crumbs" aria-label="Breadcrumbs">
        {crumbs.map((crumb, i) => (
          <Fragment key={`${i}-${crumb.label}`}>
            {i > 0 && <span className="sep">/</span>}
            {crumb.href !== undefined && i < crumbs.length - 1 ? (
              <Link href={crumb.href}>{crumb.label}</Link>
            ) : (
              <span className="current">{crumb.label}</span>
            )}
          </Fragment>
        ))}
      </nav>
      {context !== null && (
        <div className="bss-sysinfo">
          <span>
            Business date <span className="value">{context.businessDate}</span>
          </span>
          <span>
            Time zone <span className="value">{utcLabel(context.utcOffset)}</span>
          </span>
        </div>
      )}
    </header>
  );
}

export function Shell({
  context,
  children,
}: {
  context: SystemContext | null;
  children: ReactNode;
}) {
  return (
    <div className="bss-root" data-ui="ui.shell">
      {context !== null ? <Sidebar context={context} /> : <aside className="bss-sidebar" />}
      <div className="bss-main">
        <TopBar context={context} />
        <main className="bss-content">{children}</main>
      </div>
    </div>
  );
}
