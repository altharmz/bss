import { Fragment, type ReactNode } from 'react';
import { getPage } from '../api/client';
import { useResource } from '../api/useResource';
import { formatInt } from '../format/money';
import { utcLabel } from '../format/dates';
import { useCrumbs, type SystemContext } from './context';
import { Link, useRouter, type Route } from './router';
import './shell.css';

// The frame around every screen: sidebar (brand, navigation, operator) and top bar
// (breadcrumbs, business date, time zone). Billing runs is not in the navigation until
// stage 4 (decision D6).

const ACCOUNTS = '/tmf-api/accountManagement/v5/billingAccount';
const OFFERINGS = '/tmf-api/productCatalogManagement/v5/productOffering';

function useCount(path: string): string {
  const total = useResource(path, (signal) =>
    getPage(`${path}?limit=1&fields=id`, signal).then((page) => page.total),
  );
  return total.status === 'ok' ? formatInt(total.data) : '';
}

interface NavItem {
  label: string;
  href: string;
  count: string;
  active: (route: Route) => boolean;
}

export function Sidebar({ context }: { context: SystemContext }) {
  const { route } = useRouter();
  const accounts = useCount(ACCOUNTS);
  const offerings = useCount(OFFERINGS);
  const items: NavItem[] = [
    {
      label: 'Subscribers',
      href: '/subscribers',
      count: accounts,
      active: (r) => r.name === 'subscribers' || r.name === 'subscriber',
    },
    {
      label: 'Product catalog',
      href: '/catalog',
      count: offerings,
      active: (r) => r.name === 'catalog',
    },
  ];
  return (
    <aside className="bss-sidebar">
      <div className="bss-brand">
        <span className="mark">B</span>
        <span className="name">BSS Console</span>
        <span className="env">{context.environment.toUpperCase()}</span>
      </div>
      <nav className="bss-nav" aria-label="Operations">
        <div className="section">Operations</div>
        {items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={item.active(route) ? 'page' : undefined}
          >
            {item.label}
            <span className="count">{item.count}</span>
          </Link>
        ))}
      </nav>
      <div className="bss-operator">
        <div className="who">{context.operator ?? 'Local session'}</div>
        <div className="role">
          Billing operator
          {context.operator !== null && (
            <>
              {' · '}
              <span className="login">{context.operator}</span>
            </>
          )}
        </div>
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
    <div className="bss-root">
      {context !== null ? <Sidebar context={context} /> : <aside className="bss-sidebar" />}
      <div className="bss-main">
        <TopBar context={context} />
        <main className="bss-content">{children}</main>
      </div>
    </div>
  );
}
