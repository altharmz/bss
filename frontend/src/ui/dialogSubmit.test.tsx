import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Button, Dialog, DialogFooter, Modal, submitDialogPrimary } from './kit';

describe('dialog scrolling and submission', () => {
  it('activates an explicitly designated legacy footer primary without restyling it', () => {
    const submit = vi.fn();
    render(
      <Dialog
        size="S"
        title="Number"
        onClose={() => {}}
        onSubmit={submitDialogPrimary}
        footer={
          <Button data-dialog-primary onClick={submit}>
            Create
          </Button>
        }
      >
        <input aria-label="Number" />
      </Dialog>,
    );
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Number' }), { key: 'Enter' });
    expect(submit).toHaveBeenCalledOnce();
  });
  it('portals a loaded child footer outside the scrolling body inherited by Modal', () => {
    const submit = vi.fn();
    render(
      <Modal width={560} title="Edit" onClose={() => {}} onSubmit={submitDialogPrimary}>
        <div>
          <input aria-label="Field" />
          <DialogFooter>
            <Button variant="primary" onClick={submit}>
              Save
            </Button>
          </DialogFooter>
        </div>
      </Modal>,
    );
    const dialog = screen.getByRole('dialog');
    const body = dialog.querySelector('.bss-dialog-body');
    const footer = screen.getByRole('button', { name: 'Save' }).closest('footer');
    expect(footer?.parentElement).toBe(dialog);
    expect(body?.contains(footer)).toBe(false);
    expect(dialog.querySelector('header')?.parentElement).toBe(dialog);
    fireEvent.keyDown(screen.getByLabelText('Field'), { key: 'Enter' });
    expect(submit).toHaveBeenCalledOnce();
  });

  it.each(['text', 'number', 'date', 'email'])(
    'submits an enabled footer primary from a %s input',
    (type) => {
      const submit = vi.fn();
      render(
        <Dialog
          size="S"
          title="Create"
          onClose={() => {}}
          onSubmit={submitDialogPrimary}
          footer={
            <Button variant="primary" onClick={submit}>
              Create
            </Button>
          }
        >
          <input type={type} aria-label="Value" />
        </Dialog>,
      );
      fireEvent.keyDown(screen.getByLabelText('Value'), { key: 'Enter' });
      expect(submit).toHaveBeenCalledOnce();
    },
  );

  it.each(['disabled', 'destructive', 'textarea', 'checkbox', 'composing', 'opt-out'])(
    'does not submit for %s',
    (exclusion) => {
      const submit = vi.fn();
      render(
        <Dialog
          size="S"
          title="Confirm"
          onClose={() => {}}
          onSubmit={exclusion === 'opt-out' ? undefined : submitDialogPrimary}
          footer={
            <Button
              variant={exclusion === 'destructive' ? 'danger' : 'primary'}
              disabled={exclusion === 'disabled'}
              onClick={submit}
            >
              Confirm
            </Button>
          }
        >
          {exclusion === 'textarea' ? (
            <textarea aria-label="Value" />
          ) : (
            <input aria-label="Value" type={exclusion === 'checkbox' ? 'checkbox' : 'text'} />
          )}
        </Dialog>,
      );
      fireEvent.keyDown(screen.getByLabelText('Value'), {
        key: 'Enter',
        isComposing: exclusion === 'composing',
      });
      expect(submit).not.toHaveBeenCalled();
    },
  );
});
