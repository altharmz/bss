import { useWriteLock } from './WriteLock';
import { useCallback, useEffect, useEffectEvent, useState } from 'react';

// One async load per component: loading -> ok | error, with retry. `key` identifies what
// is loaded (a path, an id); a new key, or retry(), loads again and aborts the previous
// request, so a late answer never overwrites a newer one.

export type Resource<T> =
  { status: 'loading' } | { status: 'error'; error: Error } | { status: 'ok'; data: T };

export type ResourceWithRetry<T> = Resource<T> & { retry: () => void; refreshError?: Error | null };

export function useResource<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  keepPrevious = false,
): ResourceWithRetry<T> {
  const { locked } = useWriteLock();
  const paused = keepPrevious && locked;
  const [state, setState] = useState<{
    key: string;
    attempt: number;
    value: Resource<T>;
    refreshError: Error | null;
  }>({
    key,
    attempt: 0,
    value: { status: 'loading' },
    refreshError: null,
  });
  const [attempt, setAttempt] = useState(0);
  const run = useEffectEvent((signal: AbortSignal) => load(signal));
  const satisfied = useEffectEvent(
    () => state.key === key && state.attempt === attempt && state.value.status === 'ok',
  );

  useEffect(() => {
    if (paused || (keepPrevious && satisfied())) return;
    const controller = new AbortController();
    run(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) {
          setState({ key, attempt, value: { status: 'ok', data }, refreshError: null });
        }
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const value = error instanceof Error ? error : new Error(String(error));
        setState((previous) =>
          keepPrevious && previous.value.status === 'ok'
            ? { key, attempt, value: previous.value, refreshError: value }
            : { key, attempt, value: { status: 'error', error: value }, refreshError: null },
        );
      },
    );
    return () => controller.abort();
  }, [key, attempt, paused, keepPrevious]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  // A result for another key or an older attempt is stale: show loading until the
  // current load answers.
  const current =
    (state.key === key && state.attempt === attempt) ||
    (keepPrevious && state.value.status === 'ok')
      ? state.value
      : { status: 'loading' as const };
  return { ...current, retry, refreshError: state.refreshError };
}
