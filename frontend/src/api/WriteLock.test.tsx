import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { usePolled } from './usePolled';
import { useResource } from './useResource';
import { writeLock } from './WriteLock';

afterEach(() => {
  writeLock.reset();
  vi.useRealTimers();
});

it('counts nested writes and releases each lock once', () => {
  const first = writeLock.begin();
  const second = writeLock.begin();
  expect(writeLock.snapshot()).toBe(2);
  first();
  first();
  expect(writeLock.snapshot()).toBe(1);
  second();
  expect(writeLock.snapshot()).toBe(0);
});

it('pauses polling during a write and resumes after release', async () => {
  const load = vi.fn(async () => 'ready');
  function Probe() {
    const resource = usePolled('poll', load, () => true, 100);
    return <span>{resource.status}</span>;
  }
  render(<Probe />);
  await screen.findByText('ok');
  vi.useFakeTimers();
  let release!: () => void;
  act(() => {
    release = writeLock.begin();
  });
  await act(async () => {
    vi.advanceTimersByTime(500);
  });
  expect(load).toHaveBeenCalledTimes(1);
  act(() => release());
  await act(async () => {
    vi.advanceTimersByTime(100);
  });
  expect(load).toHaveBeenCalledTimes(2);
});

it('retains a mounted card during refresh and defers its read until the write ends', async () => {
  const load = vi.fn(async () => 'account');
  function Probe({ version }: { version: number }) {
    const resource = useResource(`card:${version}`, load, true);
    return <span>{resource.status === 'ok' ? resource.data : resource.status}</span>;
  }
  const { rerender } = render(<Probe version={0} />);
  await screen.findByText('account');
  let release!: () => void;
  act(() => {
    release = writeLock.begin();
  });
  rerender(<Probe version={1} />);
  expect(screen.getByText('account')).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(1);
  act(() => release());
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it('keeps the card mounted and reports a failed background refetch', async () => {
  const load = vi.fn(async () => 'account');
  function Probe({ version }: { version: number }) {
    const resource = useResource(`card:${version}`, load, true);
    return (
      <>
        <span>{resource.status === 'ok' ? resource.data : resource.status}</span>
        <span>{resource.refreshError?.message}</span>
      </>
    );
  }
  const { rerender } = render(<Probe version={0} />);
  const account = await screen.findByText('account');
  load.mockRejectedValueOnce(new Error('refresh failed'));
  rerender(<Probe version={1} />);
  await screen.findByText('refresh failed');
  expect(screen.getByText('account')).toBe(account);
});
