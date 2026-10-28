import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { Button } from '../ui/kit';
import { Footer, useWrite } from './FinancialForms';

function Harness({ action }: { action: () => Promise<string> }) {
  const write = useWrite(vi.fn());
  return (
    <footer className="bss-modal-footer">
      <Footer write={write} onRefresh={vi.fn()}>
        <Button>Cancel</Button>
        <Button variant="primary" onClick={() => void write.run(action)}>
          Record payment
        </Button>
      </Footer>
    </footer>
  );
}

describe('Ledger write footer', () => {
  it('shows the refusal message and optional presentation code above its actions', () => {
    render(
      <footer className="bss-modal-footer">
        <Footer
          write={{
            busy: false,
            unsure: false,
            problem: 'The amount exceeds the limit.',
            code: 'PAYMENT_LIMIT',
          }}
          onRefresh={vi.fn()}
        >
          <Button>Cancel</Button>
          <Button variant="primary">Record payment</Button>
        </Footer>
      </footer>,
    );
    const note = screen.getByRole('alert');
    expect(note.textContent).toBe('Refused. The amount exceeds the limit. PAYMENT_LIMIT');
    expect(within(note).getByText('PAYMENT_LIMIT').className).toBe('bss-note-code');
    expect(
      note.nextElementSibling?.contains(screen.getByRole('button', { name: 'Record payment' })),
    ).toBe(true);
  });

  it('replaces the primary with Refresh after an uncertain write and retains the failed call', async () => {
    const refresh = vi.fn();
    const { rerender } = render(
      <Harness
        action={async () => {
          throw new ApiError('POST', '/payment', 503, 'Unavailable');
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    const note = await screen.findByRole('note');
    expect(note.textContent).toBe(
      'Unsure whether it was saved. The request was sent but no answer came back. Refresh to read it back before trying again. POST /payment → 503 Unavailable',
    );
    expect(screen.queryByRole('button', { name: 'Record payment' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Refresh' }).classList.contains('primary')).toBe(
      true,
    );
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy();
    rerender(
      <Footer write={{ busy: false, unsure: true, problem: null }} onRefresh={refresh}>
        <Button>Cancel</Button>
        <Button variant="primary">Record payment</Button>
      </Footer>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('disables cancel and marks the primary busy while retaining its label', async () => {
    let finish!: (value: string) => void;
    render(
      <Harness
        action={() =>
          new Promise((resolve) => {
            finish = resolve;
          })
        }
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    const primary = screen.getByRole('button', { name: 'Record payment' }) as HTMLButtonElement;
    expect(primary.disabled).toBe(true);
    expect(primary.classList.contains('is-busy')).toBe(true);
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    finish('done');
    await screen.findByText('Not sent. Try again.');
  });

  it('shows Not sent when the confirmed callback resolves without sending a request', async () => {
    render(<Harness action={async () => 'saved'} />);
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    expect(await screen.findByText('Not sent. Try again.')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Record payment' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });
});
