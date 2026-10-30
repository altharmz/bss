import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SystemContextProvider } from '../app/context';
import { CONTEXT } from '../test/fetch';
import { WatchProgress, type WatchPhase } from './WatchProgress';

describe('accepted command steps', () => {
  it.each<[WatchPhase, string]>([
    ['waiting', 'Waiting for the worker'],
    ['funding', 'Needs top-up'],
    ['scheduled', 'Waiting for start date'],
    ['completed', 'Completed'],
    ['failed', 'Failed (technical)'],
    ['rejected', 'Failed'],
  ])('shows the %s phase without inventing a technical failure', (phase, label) => {
    render(
      <WatchProgress phase={phase} lastCheckedAt={null}>
        Server detail
      </WatchProgress>,
    );
    const steps = screen.getByRole('list', { name: 'Command steps' });
    expect(within(steps).getByText('Accepted').className).toContain('is-done');
    expect(within(steps).getByText(label).getAttribute('aria-current')).toBe('step');
    expect(screen.getByText('Server detail')).toBeTruthy();
    expect(screen.queryByText(/last check/)).toBeNull();
    if (phase !== 'failed') expect(within(steps).queryByText('Failed (technical)')).toBeNull();
  });

  it('formats the last completed check in the business time zone', () => {
    render(
      <SystemContextProvider value={CONTEXT}>
        <WatchProgress phase="waiting" lastCheckedAt="2026-10-01T06:04:05Z">
          Pending
        </WatchProgress>
      </SystemContextProvider>,
    );
    expect(screen.getByText('Checking every 5 s · last check 09:04:05')).toBeTruthy();
  });
});
