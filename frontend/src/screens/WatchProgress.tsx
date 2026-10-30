import type { ReactNode } from 'react';
import { useOptionalSystemContext } from '../app/context';
import { clockTime } from '../format/dates';

export type WatchPhase = 'waiting' | 'funding' | 'scheduled' | 'completed' | 'failed' | 'rejected';

/** Presentation only: the watch still owns its polling, actions and settlement. */
export function WatchProgress({
  phase,
  lastCheckedAt,
  children,
}: {
  phase: WatchPhase;
  lastCheckedAt: string | null;
  children: ReactNode;
}) {
  const context = useOptionalSystemContext();
  const finished = phase === 'completed' || phase === 'failed' || phase === 'rejected';
  const current = {
    waiting: 'Waiting for the worker',
    funding: 'Needs top-up',
    scheduled: 'Waiting for start date',
    completed: 'Completed',
    failed: 'Failed (technical)',
    rejected: 'Failed',
  }[phase];
  return (
    <>
      <ol className="subscriber-watch-steps" aria-label="Command steps">
        <li className="is-done">
          <span className="subscriber-step-dot" />
          Accepted
        </li>
        <li className={`is-${phase}`} aria-current="step">
          <span className="subscriber-step-dot" />
          {current}
        </li>
        {!finished && (
          <li>
            <span className="subscriber-step-dot" />
            Completed
          </li>
        )}
      </ol>
      <div className="subscriber-watch-detail">{children}</div>
      {lastCheckedAt !== null && (
        <p className="subscriber-watch-check">
          Checking every 5 s · last check {clockTime(lastCheckedAt, context?.timeZone ?? 'UTC')}
        </p>
      )}
    </>
  );
}
