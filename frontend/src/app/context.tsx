import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { components } from '../api/generated/internal';

// What the whole UI needs from GET /internal/v1/context (business date, time zone,
// environment, operator, currency), and the breadcrumbs the current screen asks for.

export type SystemContext = components['schemas']['Context'];

const SystemContextValue = createContext<SystemContext | null>(null);

/** Bootstrap errors render before the business context is available. */
export function useOptionalSystemContext(): SystemContext | null {
  return useContext(SystemContextValue);
}

export function SystemContextProvider({
  value,
  children,
}: {
  value: SystemContext;
  children: ReactNode;
}) {
  return <SystemContextValue.Provider value={value}>{children}</SystemContextValue.Provider>;
}

export function useSystemContext(): SystemContext {
  const value = useContext(SystemContextValue);
  if (!value) throw new Error('useSystemContext outside SystemContextProvider');
  return value;
}

// --- breadcrumbs: the screen declares them, the top bar shows them ---

export interface Crumb {
  label: string;
  href?: string; // parent crumbs link; the current (last) one does not
}

const CrumbsValue = createContext<{
  crumbs: Crumb[];
  setCrumbs: (crumbs: Crumb[]) => void;
}>({ crumbs: [], setCrumbs: () => {} });

export function CrumbsProvider({ children }: { children: ReactNode }) {
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  return <CrumbsValue.Provider value={{ crumbs, setCrumbs }}>{children}</CrumbsValue.Provider>;
}

export function useCrumbs(): Crumb[] {
  return useContext(CrumbsValue).crumbs;
}

/** Called by a screen with its breadcrumbs; `key` changes when they should update. */
export function useSetCrumbs(crumbs: Crumb[], key: string): void {
  const { setCrumbs } = useContext(CrumbsValue);
  useEffect(() => {
    setCrumbs(crumbs);
    // `key` stands for the crumbs' content; the array itself is new on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setCrumbs]);
}

// --- the sidebar's counts and the billing failed badge: a screen that changes what they
// show (the Billing runs screen, when its latest run changes) asks for a reload ---

const NavVersionValue = createContext<{ version: number; refresh: () => void }>({
  version: 0,
  refresh: () => {},
});

export function NavVersionProvider({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const value = useMemo(() => ({ version, refresh }), [version, refresh]);
  return <NavVersionValue.Provider value={value}>{children}</NavVersionValue.Provider>;
}

export function useNavVersion(): number {
  return useContext(NavVersionValue).version;
}

export function useRefreshNav(): () => void {
  return useContext(NavVersionValue).refresh;
}
