import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useResource } from './useResource';

// A load the test resolves or rejects by hand.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

type Deferred = ReturnType<typeof deferred<string>>;

// Renders the resource's state as text, and its retry as a button.
function Probe({ id, load }: { id: string; load: (key: string) => Promise<string> }) {
  const resource = useResource(id, () => load(id));
  const text =
    resource.status === 'ok'
      ? resource.data
      : resource.status === 'error'
        ? `error: ${resource.error.message}`
        : 'loading';
  return (
    <>
      <span data-testid="state">{text}</span>
      <button type="button" onClick={resource.retry}>
        retry
      </button>
    </>
  );
}

const state = () => screen.getByTestId('state').textContent;

describe('useResource', () => {
  it('goes loading -> ok', async () => {
    const d = deferred<string>();
    render(<Probe id="a" load={() => d.promise} />);
    expect(state()).toBe('loading');
    await act(async () => d.resolve('data A'));
    expect(state()).toBe('data A');
  });

  it('goes loading -> error, and retry loads again', async () => {
    const loads = [deferred<string>(), deferred<string>()];
    const [first, second] = loads as [Deferred, Deferred];
    render(<Probe id="a" load={() => (loads.shift() as Deferred).promise} />);
    await act(async () => first.reject(new Error('boom')));
    expect(state()).toBe('error: boom');
    fireEvent.click(screen.getByRole('button', { name: 'retry' }));
    expect(state()).toBe('loading');
    await act(async () => second.resolve('fixed'));
    expect(state()).toBe('fixed');
  });

  it('does not keep showing the data of the old key while the new key loads', async () => {
    const pending: Record<string, Deferred> = { a: deferred<string>(), b: deferred<string>() };
    const load = (key: string) => (pending[key] as Deferred).promise;
    const { rerender } = render(<Probe id="a" load={load} />);
    await act(async () => pending.a?.resolve('data A'));
    expect(state()).toBe('data A');
    rerender(<Probe id="b" load={load} />); // e.g. another subscriber's card
    expect(state()).toBe('loading');
    await act(async () => pending.b?.resolve('data B'));
    expect(state()).toBe('data B');
  });

  it('never shows a stale answer for an old key', async () => {
    const pending: Record<string, Deferred> = { a: deferred<string>(), b: deferred<string>() };
    const load = (key: string) => (pending[key] as Deferred).promise;
    const { rerender } = render(<Probe id="a" load={load} />);
    rerender(<Probe id="b" load={load} />);
    await act(async () => pending.a?.resolve('late A'));
    expect(state()).toBe('loading');
    await act(async () => pending.b?.resolve('data B'));
    expect(state()).toBe('data B');
  });
});
