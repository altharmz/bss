import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { UiKitScreen } from '../screens/UiKit';
import { Id, ID_PREFIXES } from './Id';
import { ToastProvider } from './kit';

const id = 'abcdef01-0000-4000-8000-000000000001';

describe('Ledger Id', () => {
  it('previews amounts and every identifier prefix in the gallery', () => {
    render(<UiKitScreen />);
    const section = screen.getByRole('heading', { name: 'Amounts and ids' }).closest('section');
    if (!section) throw new Error('Identifier gallery section is missing');
    const gallery = within(section);
    expect(screen.getByText('−42.10')).toBeTruthy();
    expect(screen.getByText('+10.00')).toBeTruthy();
    expect(
      gallery.getAllByRole('button', { name: 'Copy 81d0e7b4-0000-4000-8000-000000000001' }),
    ).toHaveLength(ID_PREFIXES.length);
  });
  it.each(ID_PREFIXES)('displays %s with the short uppercase body', (prefix) => {
    render(<Id id={id} prefix={prefix} />);
    const button = screen.getByRole('button', { name: `Copy ${id}` });
    expect(button.textContent).toBe(`${prefix}ABCDEF01`);
    expect(button.firstElementChild?.className).toBe('id-prefix');
    expect(button.lastElementChild?.className).toBe('id-body');
  });
  it('copies the full UUID, toasts success and does not open the enclosing row', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const rowClick = vi.fn();
    render(
      <ToastProvider>
        <div onClick={rowClick}>
          <Id id={id} prefix="BA-" />
        </div>
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: `Copy ${id}` }));
    expect(writeText).toHaveBeenCalledWith(id);
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Copied');
    expect(rowClick).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
  it('reports clipboard failure', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) },
    });
    render(
      <ToastProvider>
        <Id id={id} prefix="ORD-" />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: `Copy ${id}` }));
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Copy failed');
    vi.unstubAllGlobals();
  });
});
