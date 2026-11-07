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
  | { name: 'numbers' }
  | { name: 'sims' }
  | { name: 'ports' }
  | { name: 'subscribers' }
  | { name: 'subscriber'; accountId: string }
  | { name: 'catalog' }
  | { name: 'orders' }
  | { name: 'products' }
  | { name: 'billingRuns' }
  | { name: 'prepaidCommands' }
  | { name: 'financialAccounts' }
  | { name: 'parties' }
  | { name: 'billingSettings' }
  | { name: 'payments' }
  | { name: 'bills' }
  | { name: 'balances' }
  | { name: 'integrations' }
  | { name: 'notFound'; path: string };

const SUBSCRIBER = /^\/subscribers\/([0-9a-fA-F-]{36})\/?$/;

export function parseRoute(path: string): Route {
  if (path === '/ui-kit' || path === '/ui-kit/') return { name: 'uiKit' };
  if (path === '/sign-in') return { name: 'signIn' };
  if (path === '/change-password') return { name: 'password' };
  if (path === '/users') return { name: 'users' };
  if (path === '/integrations' || path === '/integrations/') return { name: 'integrations' };
  if (path === '/numbers' || path === '/numbers/') return { name: 'numbers' };
  if (path === '/sims' || path === '/sims/') return { name: 'sims' };
  if (path === '/ports' || path === '/ports/') return { name: 'ports' };
  if (path === '/subscribers' || path === '/subscribers/') return { name: 'subscribers' };
  const card = SUBSCRIBER.exec(path);
  if (card?.[1]) return { name: 'subscriber', accountId: card[1].toLowerCase() };
  if (path === '/catalog' || path === '/catalog/') return { name: 'catalog' };
  if (path === '/orders' || path === '/orders/') return { name: 'orders' };
  if (path === '/products' || path === '/products/') return { name: 'products' };
  if (path === '/billing-runs' || path === '/billing-runs/') return { name: 'billingRuns' };
  if (path === '/prepaid-commands' || path === '/prepaid-commands/') {
    return { name: 'prepaidCommands' };
  }
  if (path === '/financial-accounts' || path === '/financial-accounts/') {
    return { name: 'financialAccounts' };
  }
  if (path === '/parties' || path === '/parties/') return { name: 'parties' };
  if (path === '/billing-settings' || path === '/billing-settings/') {
    return { name: 'billingSettings' };
  }
  if (path === '/payments' || path === '/payments/') return { name: 'payments' };
  if (path === '/bills' || path === '/bills/') return { name: 'bills' };
  if (path === '/balances' || path === '/balances/') return { name: 'balances' };
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
    case 'numbers':
      return '/numbers';
    case 'sims':
      return '/sims';
    case 'ports':
      return '/ports';
    case 'subscribers':
      return '/subscribers';
    case 'subscriber':
      return `/subscribers/${route.accountId}`;
    case 'catalog':
      return '/catalog';
    case 'orders':
      return '/orders';
    case 'products':
      return '/products';
    case 'billingRuns':
      return '/billing-runs';
    case 'prepaidCommands':
      return '/prepaid-commands';
    case 'financialAccounts':
      return '/financial-accounts';
    case 'parties':
      return '/parties';
    case 'billingSettings':
      return '/billing-settings';
    case 'payments':
      return '/payments';
    case 'bills':
      return '/bills';
    case 'balances':
      return '/balances';
  }
}

interface RouterValue {
  route: Route;
  navigate: (href: string) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function currentPath(): string {
  // "/" has no screen of its own: it is the subscribers list
  const path = window.location.pathname;
  if (path === '/') {
    window.history.replaceState(null, '', '/subscribers');
    return '/subscribers';
  }
  return path;
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
