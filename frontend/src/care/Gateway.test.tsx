import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Gateway } from './Gateway';

function frame() {
  return screen.getByTitle('Test payment gateway') as HTMLIFrameElement;
}

function post(data: unknown, init: { origin?: string; source?: Window | null } = {}) {
  window.dispatchEvent(
    new MessageEvent('message', {
      data,
      origin: init.origin ?? location.origin,
      source: init.source === undefined ? frame().contentWindow : init.source,
    }),
  );
}

describe('Gateway', () => {
  it('loads the stub gateway and passes on the token its frame posts', () => {
    const onToken = vi.fn();
    render(<Gateway onToken={onToken} />);
    expect(frame().getAttribute('src')).toBe('/care/stub-gateway.html');
    post({ type: 'stub-card-token', token: 'tok_4242' });
    expect(onToken).toHaveBeenCalledExactlyOnceWith('tok_4242');
  });

  it.each([
    ['another origin', { type: 'stub-card-token', token: 't' }, { origin: 'https://evil.test' }],
    ['another window', { type: 'stub-card-token', token: 't' }, { source: window }],
    ['no source', { type: 'stub-card-token', token: 't' }, { source: null }],
    ['another message type', { type: 'resize', token: 't' }, {}],
    ['a token that is not text', { type: 'stub-card-token', token: 42 }, {}],
    ['no data', null, {}],
  ])('ignores a message from %s', (_, data, init) => {
    const onToken = vi.fn();
    render(<Gateway onToken={onToken} />);
    post(data, init);
    expect(onToken).not.toHaveBeenCalled();
  });

  it('hands a token to the current callback only', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<Gateway onToken={first} />);
    rerender(<Gateway onToken={second} />);
    post({ type: 'stub-card-token', token: 'tok_1' });
    expect(second).toHaveBeenCalledExactlyOnceWith('tok_1');
    expect(first).not.toHaveBeenCalled();
  });
});
