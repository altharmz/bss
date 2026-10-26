import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Button, Modal, StatusPill, Segmented, ToastProvider, useToast } from './kit';

describe('StatusPill', () => {
  it('uses the explicit product family and preserves the note label', () => {
    render(<StatusPill kind="product" status="pendingTerminate" label="ends 2026-10-25" />);
    expect(screen.getByText('ends 2026-10-25').className).toBe('status-pill pill-pending');
  });
});

describe('Segmented', () => {
  it('marks the selected option and reports a pick', () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Status"
        value="All"
        onChange={onChange}
        options={[
          { key: 'All', label: 'All', count: 3 },
          { key: 'Active', label: 'Active', count: 2 },
        ]}
      />,
    );
    const all = screen.getByRole('button', { name: /^All/ });
    const active = screen.getByRole('button', { name: /^Active/ });
    expect(all.getAttribute('aria-pressed')).toBe('true');
    expect(active.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(active);
    expect(onChange).toHaveBeenCalledWith('Active');
  });
});

describe('Button', () => {
  it('is a non-submitting button with its variant', () => {
    render(
      <Button variant="quiet" tone="negative" small disabled>
        Deactivate
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Deactivate' }) as HTMLButtonElement;
    expect(button.type).toBe('button');
    expect(button.className).toBe('bss-btn quiet small tone-negative');
    expect(button.disabled).toBe(true);
  });
});

describe('Modal', () => {
  it('closes on ×, on the overlay and on Escape - not on a click inside', () => {
    const onClose = vi.fn();
    render(
      <Modal title="Add product" width={720} onClose={onClose}>
        <p>body</p>
      </Modal>,
    );
    fireEvent.click(screen.getByText('body'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('dialog').parentElement as HTMLElement);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('dialog').style.getPropertyValue('--modal-width')).toBe('720px');
  });
});

describe('Toast', () => {
  function Trigger() {
    const show = useToast();
    return <Button onClick={() => show('Run for 2026-09-26 started')}>go</Button>;
  }

  it('shows one message and hides it after 3.2 s', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    expect(screen.getByRole('status').textContent).toBe('Run for 2026-09-26 started');
    act(() => vi.advanceTimersByTime(3199));
    expect(screen.queryByRole('status')).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole('status')).toBeNull();
  });
});
