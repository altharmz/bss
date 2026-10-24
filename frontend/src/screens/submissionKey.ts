import { useRef } from 'react';
import { ApiError, newIdempotencyKey } from '../api/client';

// The Idempotency-Key discipline of the prepaid money forms (frontend.md P6b, P6c1): one key
// per submission. After an uncertain outcome the same unchanged request is sent again with
// the same key, so the server replays what it stored instead of acting twice; an edited
// request, a refusal or a success starts a new key.

/** No response or a 5xx: the request may or may not have been recorded. */
export const uncertain = (error: unknown) =>
  !(error instanceof ApiError) || error.status === 0 || error.status >= 500;

export function useSubmissionKey() {
  const pending = useRef<{ key: string; request: string } | null>(null);
  return {
    /** The key for this exact request: the pending one if it is the same request. */
    keyFor(request: string): string {
      const key =
        pending.current !== null && pending.current.request === request
          ? pending.current.key
          : newIdempotencyKey();
      pending.current = { key, request };
      return key;
    },
    /** The submission ended (answered, refused or succeeded): the next one is new. */
    settle() {
      pending.current = null;
    },
  };
}
