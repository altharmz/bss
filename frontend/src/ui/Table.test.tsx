import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TableHead, TableRow } from './Table';

const columns = [{ key: 'name', label: 'Name', width: '1fr' }];

describe('Ledger table rows', () => {
  it('activates a clickable row by Enter and Space and exposes selection', () => {
    const activate = vi.fn();
    render(
      <TableRow
        columns={columns}
        cells={{ name: 'Ada' }}
        selected
        density="compact"
        onClick={activate}
      />,
    );
    const row = screen.getByRole('row');
    expect(row.tabIndex).toBe(0);
    expect(row.getAttribute('aria-selected')).toBe('true');
    expect(row.classList.contains('selected')).toBe(true);
    expect(row.classList.contains('density-compact')).toBe(true);
    fireEvent.keyDown(row, { key: 'Enter' });
    fireEvent.keyDown(row, { key: ' ' });
    expect(activate).toHaveBeenCalledTimes(2);
  });

  it('does not activate a row from a nested control key event', () => {
    const activate = vi.fn();
    render(
      <TableRow columns={columns} cells={{ name: <button>Copy</button> }} onClick={activate} />,
    );
    fireEvent.keyDown(screen.getByRole('button'), { key: 'Enter' });
    expect(activate).not.toHaveBeenCalled();
  });

  it('keeps static rows outside the tab order and supports dense headers', () => {
    const { rerender } = render(<TableRow columns={columns} cells={{ name: 'Ada' }} />);
    expect(screen.getByRole('row').hasAttribute('tabindex')).toBe(false);
    rerender(<TableHead columns={columns} density="dense" />);
    expect(screen.getByRole('row').classList.contains('density-dense')).toBe(true);
  });
});

describe('Table cells (B16, B17)', () => {
  it('ellipsizes a name cell with the full text in title and never wraps a date', () => {
    const dated = [
      { key: 'name', label: 'Name', width: '1fr' },
      { key: 'date', label: 'Date', width: '100px' },
    ];
    const { container } = render(
      <TableRow
        columns={dated}
        cells={{ name: 'A very long customer name', date: '2026-10-27' }}
      />,
    );
    const cells = container.querySelectorAll('[role="cell"]');
    expect(cells[0]?.className).toContain('name');
    expect(cells[0]?.getAttribute('title')).toBe('A very long customer name');
    expect(cells[1]?.className).toContain('nowrap');
  });
});
