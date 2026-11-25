import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from 'react';

// A minimal History-API router: five routes do not need a library. Deep links work in
// production because the shared Caddy answers unknown paths with index.html (try_files).
// Billing runs lives at /billing-runs (stage 4, slice g): not /billing, so it can never be
// mistaken for the app's /billing-status, which Caddy routes to the backend.

export type Route =
  | { name: 'uiKit' }
  | { name: 'signIn' }
  | { name: 'password' }
  | { name: 'users' }
  | { name: 'logging' }
  | { name: 'numbers' }
  | { name: 'sims' }
  | { name: 'ports' }
  | { name: 'subscribers' }
  | { name: 'subscriber'; accountId: string }
  | { name: 'catalog' }
  | { name: 'orders' }
  | { name: 'products' }
  | { name: 'product'; productId: string }
  | { name: 'billingRuns' }
  | { name: 'prepaidCommands' }
  | { name: 'financialAccounts' }
  | { name: 'careSignups' }
  | { name: 'parties' }
  | { name: 'customers' }
  | { name: 'settings'; tab: SettingsTab }
  | { name: 'payments' }
  | { name: 'dunning' }
  | { name: 'bills' }
  | { name: 'wholesale'; invoiceId?: string }
  | { name: 'balances' }
  | { name: 'reports' }
  | { name: 'integrations' }
  | { name: 'notFound'; path: string };

/** The Settings page's tabs (#467), each at /settings/<path>; /settings opens the first. */
export const SETTINGS_TABS = {
  billing: { path: 'billing', label: 'Billing settings' },
  prepaidLifecycle: { path: 'prepaid-lifecycle', label: 'Prepaid lifecycle' },
  dunning: { path: 'dunning', label: 'Dunning settings' },
} as const;
export type SettingsTab = keyof typeof SETTINGS_TABS;
const SETTINGS = /^\/settings(?:\/([a-z-]+))?\/?$/;

// The screens that moved into Settings (#467): the old paths are replaced by their tab
// without a history entry (currentPath), so bookmarks keep working.
const MOVED_TO_SETTINGS: Record<string, string> = {
  '/billing-settings': '/settings/billing',
  '/prepaid-lifecycle': '/settings/prepaid-lifecycle',
};

// The billing account list and card (#457, "Subscribers" before): /subscribers and
// /subscribers/{id} still open them, replaced by the new path without a history entry
// (currentPath), so old links and the MCP and notification links keep working.
const SUBSCRIBER = /^\/(?:billing-accounts|subscribers)\/([0-9a-fA-F-]{36})\/?$/;
const LEGACY_SUBSCRIBERS = /^\/subscribers(?=\/|$)/;
// One product's page (#458), off the sidebar and active under Products.
const PRODUCT = /^\/products\/([0-9a-fA-F-]{36})\/?$/;

export function parseRoute(path: string): Route {
  if (path === '/ui-kit' || path === '/ui-kit/') return { name: 'uiKit' };
  if (path === '/sign-in') return { name: 'signIn' };
  if (path === '/change-password') return { name: 'password' };
  if (path === '/users') return { name: 'users' };
  if (path === '/integrations' || path === '/integrations/') return { name: 'integrations' };
  if (path === '/numbers' || path === '/numbers/') return { name: 'numbers' };
  if (path === '/sims' || path === '/sims/') return { name: 'sims' };
  if (path === '/signups' || path === '/signups/') return { name: 'careSignups' };
  if (path === '/ports' || path === '/ports/') return { name: 'ports' };
  if (
    path === '/billing-accounts' ||
    path === '/billing-accounts/' ||
    path === '/subscribers' ||
    path === '/subscribers/'
  ) {
    return { name: 'subscribers' };
  }
  const card = SUBSCRIBER.exec(path);
  if (card?.[1]) return { name: 'subscriber', accountId: card[1].toLowerCase() };
  if (path === '/catalog' || path === '/catalog/') return { name: 'catalog' };
  if (path === '/orders' || path === '/orders/') return { name: 'orders' };
  if (path === '/products' || path === '/products/') return { name: 'products' };
  const product = PRODUCT.exec(path);
  if (product?.[1]) return { name: 'product', productId: product[1].toLowerCase() };
  if (path === '/billing-runs' || path === '/billing-runs/') return { name: 'billingRuns' };
  if (path === '/prepaid-commands' || path === '/prepaid-commands/') {
    return { name: 'prepaidCommands' };
  }
  if (path === '/financial-accounts' || path === '/financial-accounts/') {
    return { name: 'financialAccounts' };
  }
  if (path === '/parties' || path === '/parties/') return { name: 'parties' };
  if (path === '/customers' || path === '/customers/') return { name: 'customers' };
  if (path === '/settings/logging' || path === '/settings/logging/') return { name: 'logging' };
  const settings = SETTINGS.exec(path);
  if (settings) {
    if (settings[1] === undefined) return { name: 'settings', tab: 'billing' };
    const tab = (Object.keys(SETTINGS_TABS) as SettingsTab[]).find(
      (key) => SETTINGS_TABS[key].path === settings[1],
    );
    if (tab) return { name: 'settings', tab };
  }
  if (path === '/payments' || path === '/payments/') return { name: 'payments' };
  if (path === '/dunning' || path === '/dunning/') return { name: 'dunning' };
  if (path === '/bills' || path === '/bills/') return { name: 'bills' };
  if (path === '/wholesale' || path === '/wholesale/') return { name: 'wholesale' };
  const invoice = /^\/wholesale\/([0-9a-fA-F-]{36})\/?$/.exec(path);
  if (invoice?.[1]) return { name: 'wholesale', invoiceId: invoice[1].toLowerCase() };
  if (path === '/balances' || path === '/balances/') return { name: 'balances' };
  if (path === '/reports' || path === '/reports/') return { name: 'reports' };
  return { name: 'notFound', path };
}

export function hrefOf(route: Exclude<Route, { name: 'notFound' }>): string {
  switch (route.name) {
    case 'uiKit':
      return '/ui-kit';
    case 'signIn':
      return '/sign-in';
    case 'password':
      return '/change-password';
    case 'users':
      return '/users';
    case 'integrations':
      return '/integrations';
    case 'logging':
      return '/settings/logging';
    case 'numbers':
      return '/numbers';
    case 'sims':
      return '/sims';
    case 'ports':
      return '/ports';
    case 'subscribers':
      return '/billing-accounts';
    case 'subscriber':
      return `/billing-accounts/${route.accountId}`;
    case 'catalog':
      return '/catalog';
    case 'orders':
      return '/orders';
    case 'products':
      return '/products';
    case 'product':
      return `/products/${route.productId}`;
    case 'billingRuns':
      return '/billing-runs';
    case 'prepaidCommands':
      return '/prepaid-commands';
    case 'financialAccounts':
      return '/financial-accounts';
    case 'careSignups':
      return '/signups';
    case 'parties':
      return '/parties';
    case 'customers':
      return '/customers';
    case 'settings':
      return `/settings/${SETTINGS_TABS[route.tab].path}`;
    case 'payments':
      return '/payments';
    case 'dunning':
      return '/dunning';
    case 'bills':
      return '/bills';
    case 'wholesale':
      return route.invoiceId === undefined ? '/wholesale' : `/wholesale/${route.invoiceId}`;
    case 'balances':
      return '/balances';
    case 'reports':
      return '/reports';
  }
}

interface RouterValue {
  route: Route;
  navigate: (href: string) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function currentPath(): string {
  // "/" has no screen of its own: it is the billing account list
  const { pathname, search, hash } = window.location;
  if (pathname === '/') {
    window.history.replaceState(null, '', '/billing-accounts');
    return '/billing-accounts';
  }
  const moved = MOVED_TO_SETTINGS[pathname.replace(/(.)\/$/, '$1')];
  if (moved !== undefined) {
    window.history.replaceState(null, '', `${moved}${search}${hash}`);
    return moved;
  }
  if (LEGACY_SUBSCRIBERS.test(pathname)) {
    const path = pathname.replace(LEGACY_SUBSCRIBERS, '/billing-accounts');
    window.history.replaceState(null, '', `${path}${search}${hash}`);
    return path;
  }
  return pathname;
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [path, setPath] = useState(currentPath);

  useEffect(() => {
    const onPop = () => setPath(currentPath());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((href: string) => {
    window.history.pushState(null, '', href);
    setPath(currentPath());
    window.scrollTo(0, 0);
  }, []);

  const value = useMemo(() => ({ route: parseRoute(path), navigate }), [path, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error('useRouter outside RouterProvider');
  return value;
}

/** An <a> that navigates in place; modified clicks (new tab, ...) keep browser behaviour. */
export function Link({
  href,
  onClick,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const { navigate } = useRouter();
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={handle} {...rest} />;
}
