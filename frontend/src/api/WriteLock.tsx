import { createContext, useContext, useSyncExternalStore, useEffect, type ReactNode } from 'react';

// The transport and dialogs share one counter: nested writes release independently.
let count = 0;
let sent = 0;
let generation = 0;
const deferred = new Set<() => void>();
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());
export const writeLock = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  snapshot: () => count,
  sent: () => sent,
  reset() {
    generation += 1;
    count = 0;
    deferred.clear();
    notify();
  },
  afterWrites(action: () => void) {
    if (count === 0) action();
    else deferred.add(action);
  },
  begin() {
    const owner = generation;
    count += 1;
    notify();
    let released = false;
    return () => {
      if (released || owner !== generation) return;
      released = true;
      count -= 1;
      notify();
      if (count === 0) {
        const actions = [...deferred];
        deferred.clear();
        actions.forEach((action) => action());
      }
    };
  },
  requested() {
    sent += 1;
  },
};
const WriteLock = createContext(writeLock);
export function WriteLockProvider({ children }: { children: ReactNode }) {
  useEffect(() => () => writeLock.reset(), []);
  return <WriteLock.Provider value={writeLock}>{children}</WriteLock.Provider>;
}
export function useWriteLock() {
  const lock = useContext(WriteLock);
  const count = useSyncExternalStore(lock.subscribe, lock.snapshot);
  return { locked: count > 0, begin: lock.begin };
}
