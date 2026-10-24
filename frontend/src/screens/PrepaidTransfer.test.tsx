import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { json, stubFetch, tmfError } from '../test/fetch';
import { TransferWatch } from './PrepaidTransfer';
import { POLL_CAP_MS, watch as follow } from './watched';

const ID = 'f0000001-0000-4000-8000-000000000001';
const URL = `GET /tmf-api/prepayBalanceManagement/v4/transferBalance/${ID}`;

async function until(check: () => boolean) {
  for (let i = 0; i < 100; i++) {
    if (check()) return;
    await act(async () => {
      await Promise.resolve();
    });
  }
  throw new Error('condition not reached');
}

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

function watch() {
  const onSettled = vi.fn();
  const onDismiss = vi.fn();
  const view = render(
    <TransferWatch
      watched={follow('transfer', ID, '30.00 USD to 79991234001')}
      onSettled={onSettled}
      onDismiss={onDismiss}
    />,
  );
  return { onSettled, onDismiss, view };
}

describe('following an accepted transfer', () => {
  afterEach(() => vi.useRealTimers());

  it('polls every 5 seconds and settles once the worker has completed it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let status = 'created';
    const { calls } = stubFetch({ [URL]: () => json({ id: ID, status }) });
    const { onSettled } = watch();
    await advance(1); // the first answer arrives and arms the timer
    await advance(4_000);
    expect(calls).toHaveLength(1); // the next look is at 5 s
    await advance(1_000);
    expect(calls).toHaveLength(2);
    status = 'completed';
    await advance(5_000);
    await until(() => onSettled.mock.calls.length > 0);
    expect(onSettled).toHaveBeenCalledOnce();
    expect(onSettled).toHaveBeenCalledWith('Transfer of 30.00 USD to 79991234001 completed');
    await advance(30_000);
    expect(calls).toHaveLength(3); // done: no more requests
  });

  it('reports a refused transfer with the description the API stored', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    stubFetch({
      [URL]: () =>
        json({ id: ID, status: 'failed', description: 'the receiver account is closed' }),
    });
    const { onSettled } = watch();
    await until(() => onSettled.mock.calls.length > 0);
    expect(onSettled).toHaveBeenCalledWith(
      'Transfer of 30.00 USD to 79991234001 failed: the receiver account is closed',
    );
  });

  it('stops after three minutes, says the worker still finishes it, and checks on demand', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { calls } = stubFetch({ [URL]: () => json({ id: ID, status: 'created' }) });
    const { onSettled, onDismiss, view } = watch();
    await advance(1); // the first answer arrives and arms the timer
    for (let at = 0; at < POLL_CAP_MS; at += 5_000) await advance(5_000); // a render between looks
    await until(() => screen.queryByText(/stays held/) !== null);
    expect(screen.getByRole('status').textContent).not.toMatch(/failed|could not/i);
    const count = calls.length;
    expect(count).toBeGreaterThan(30);
    expect(count).toBeLessThanOrEqual(38); // 5 s apart for 180 s, not a request per tick
    await advance(60_000);
    expect(calls).toHaveLength(count);
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await until(() => calls.length > count);
    expect(onSettled).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledOnce();
    view.unmount();
    const last = calls.length;
    await advance(60_000);
    expect(calls).toHaveLength(last);
  });

  it('says it could not check, instead of polling blindly, when a look fails', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let calls = 0;
    stubFetch({
      [URL]: () =>
        ++calls === 1 ? json({ id: ID, status: 'created' }) : tmfError(503, 'Unavailable'),
    });
    const { onSettled } = watch();
    await advance(1); // the first answer arrives and arms the timer
    await advance(5_000);
    await until(() => screen.queryByText(/Could not check the transfer/) !== null);
    const failed = calls;
    await advance(30_000);
    expect(calls).toBe(failed);
    expect(onSettled).not.toHaveBeenCalled();
  });
});
