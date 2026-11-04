import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Button, Dialog, StatusPill, Segmented, ToastProvider, useToast } from './kit';

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

describe('Dialog closing', () => {
  it('closes on ×, on the overlay and on Escape - not on a click inside', () => {
    const onClose = vi.fn();
    render(
      <Dialog title="Add product" size="L" onClose={onClose}>
        <p>body</p>
      </Dialog>,
    );
    fireEvent.click(screen.getByText('body'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('dialog').parentElement as HTMLElement);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('dialog').classList.contains('bss-dialog-L')).toBe(true);
  });
});

describe('Dialog', () => {
  it('renders context, step, padded body, footer and marker with its size', () => {
    render(
      <Dialog
        size="M"
        title="Transfer"
        context="Ivan · BA-12345678"
        step={{ n: 2, of: 3 }}
        ui="ui.transfer.test"
        onClose={() => {}}
        footer={<Button>Next</Button>}
      >
        Amount
      </Dialog>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Transfer' });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.getAttribute('data-ui')).toBe('ui.transfer.test');
    expect(dialog.classList.contains('bss-dialog-M')).toBe(true);
    expect(screen.getByText('Step 2 of 3')).not.toBeNull();
    expect(screen.getByText('Ivan · BA-12345678').className).toBe('subtitle');
    expect(screen.getByText('Amount').className).toBe('bss-dialog-body');
    expect(screen.getByRole('button', { name: 'Next' }).parentElement?.className).toBe(
      'bss-modal-footer',
    );
  });

  it('keeps static gallery previews non-modal and ignores Escape', () => {
    const onClose = vi.fn();
    render(
      <Dialog preview size="S" title="Preview" onClose={onClose}>
        Body
      </Dialog>,
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog').hasAttribute('aria-modal')).toBe(false);
  });
});

describe('Toast', () => {
  function Trigger() {
    const show = useToast();
    return <Button onClick={() => show('Run for 2026-09-26 started')}>go</Button>;
  }

  it('shows one message and hides it after 6 s', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'go' }));
    expect(screen.getByRole('status').textContent).toBe('Run for 2026-09-26 started');
    act(() => vi.advanceTimersByTime(5999));
    expect(screen.queryByRole('status')).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole('status')).toBeNull();
    vi.useRealTimers();
  });

  it('runs the action and replaces both message and action with a fresh timeout', () => {
    vi.useFakeTimers();
    const onClick = vi.fn();
    function Actions() {
      const show = useToast();
      return (
        <>
          <Button onClick={() => show('Payment recorded', { label: 'View', onClick })}>
            first
          </Button>
          <Button onClick={() => show('Copied')}>second</Button>
        </>
      );
    }
    render(
      <ToastProvider>
        <Actions />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'first' }));
    fireEvent.click(screen.getByRole('button', { name: 'View' }));
    expect(onClick).toHaveBeenCalledOnce();
    act(() => vi.advanceTimersByTime(5000));
    fireEvent.click(screen.getByRole('button', { name: 'second' }));
    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(screen.getByRole('status').textContent).toBe('Copied');
    expect(screen.queryByRole('button', { name: 'View' })).toBeNull();
    act(() => vi.advanceTimersByTime(5999));
    expect(screen.getByRole('status').textContent).toBe('Copied');
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole('status')).toBeNull();
    vi.useRealTimers();
  });
});
