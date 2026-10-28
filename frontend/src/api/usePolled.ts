import { useWriteLock } from './WriteLock';
import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react';
import type { Resource } from './useResource';

// useResource for data that changes on its own (a billing run in progress): the first
// load shows loading -> ok | error, like useResource; after that, refreshes happen in the
// background and keep showing the last data. While `active(data)`, it refreshes every
// `intervalMs` (a timer armed after each answer, so requests never pile up). A failed
// refresh keeps the data, stops the polling and is reported as `refreshError` until the
// next refresh. With `until` (epoch ms) it also stops once that time has passed and reports
// `capped`: a bounded wait (a transfer the worker completes within minutes). retry() reloads from scratch; refresh() reloads in the background now.

export type Polled<T> = Resource<T> & {
  refreshError: Error | null;
  capped: boolean; // `until` has passed: polling stopped, the data may be out of date
  retry: () => void;
  refresh: () => void;
};

interface State<T> {
  id: string; // key#attempt the value belongs to
  value: Resource<T>;
  version: number; // +1 per answer, so the timer re-arms after each one
  refreshError: Error | null;
}

const asError = (error: unknown) => (error instanceof Error ? error : new Error(String(error)));

export function usePolled<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  active: (data: T) => boolean,
  intervalMs: number,
  until?: number,
): Polled<T> {
  const { locked } = useWriteLock();
  const [attempt, setAttempt] = useState(0);
  const [tick, setTick] = useState(0);
  const id = `${key}#${attempt}`;
  const idRef = useRef(id);
  const [state, setState] = useState<State<T>>({
    id,
    value: { status: 'loading' },
    version: 0,
    refreshError: null,
  });
  const [cappedAt, setCappedAt] = useState<number | undefined>(undefined);
  const capped = until !== undefined && cappedAt === until;
  const run = useEffectEvent((signal: AbortSignal) => load(signal));

  // the first load of a key (or a retry): from scratch
  useEffect(() => {
    idRef.current = id;
    const controller = new AbortController();
    run(controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        setState((s) => ({
          id,
          value: { status: 'ok', data },
          version: s.version + 1,
          refreshError: null,
        }));
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState((s) => ({
          id,
          value: { status: 'error', error: asError(error) },
          version: s.version + 1,
          refreshError: null,
        }));
      },
    );
    return () => controller.abort();
  }, [id]);

  // a background refresh: never shows loading, only replaces the data of the same key
  useEffect(() => {
    if (tick === 0 || locked) return;
    const owner = idRef.current;
    const controller = new AbortController();
    run(controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        setState((s) =>
          s.id === owner && s.value.status === 'ok'
            ? { ...s, value: { status: 'ok', data }, version: s.version + 1, refreshError: null }
            : s,
        );
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState((s) =>
          s.id === owner && s.value.status === 'ok'
            ? { ...s, version: s.version + 1, refreshError: asError(error) }
            : s,
        );
      },
    );
    return () => controller.abort();
  }, [tick, locked]);

  const current: State<T> =
    state.id === id ? state : { id, value: { status: 'loading' }, version: 0, refreshError: null };
  const polling =
    current.value.status === 'ok' &&
    current.refreshError === null &&
    !capped &&
    active(current.value.data);

  useEffect(() => {
    if (!polling || locked) return;
    const timer = setTimeout(() => setTick((n) => n + 1), intervalMs);
    return () => clearTimeout(timer);
  }, [polling, current.version, intervalMs, locked]);

  useEffect(() => {
    if (until === undefined) return;
    const timer = setTimeout(() => setCappedAt(until), Math.max(0, until - Date.now()));
    return () => clearTimeout(timer);
  }, [until]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const refresh = useCallback(() => setTick((n) => n + 1), []);
  return { ...current.value, refreshError: current.refreshError, capped, retry, refresh };
}
