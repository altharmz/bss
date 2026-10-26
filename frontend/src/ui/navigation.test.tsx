import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Menu, TabPanel, Tabs } from './kit';
import { TableFooter, TableRow } from './Table';

function Sections() {
  const [value, setValue] = useState('overview');
  return (
    <>
      <Tabs
        id="account"
        label="Account sections"
        value={value}
        onChange={setValue}
        options={[
          { key: 'overview', label: 'Overview' },
          { key: 'products', label: 'Products', count: 2 },
          { key: 'orders', label: 'Orders' },
        ]}
      />
      <TabPanel id="account" value={value}>
        {value}
      </TabPanel>
    </>
  );
}

describe('Ledger navigation', () => {
  it('selects and focuses tabs with arrows and Home/End and links the panel', () => {
    render(<Sections />);
    const tabs = screen.getAllByRole('tab');
    for (const [key, index] of [
      ['ArrowRight', 1],
      ['End', 2],
      ['ArrowRight', 0],
      ['ArrowLeft', 2],
      ['Home', 0],
    ] as const) {
      const selected = screen.getByRole('tab', { selected: true });
      fireEvent.keyDown(selected, { key });
      expect(document.activeElement).toBe(tabs[index]);
      expect(tabs[index]?.getAttribute('aria-selected')).toBe('true');
      expect(tabs[index]?.tabIndex).toBe(0);
      expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe(tabs[index]?.id);
      expect(tabs[index]?.getAttribute('aria-controls')).toBe(screen.getByRole('tabpanel').id);
    }
  });

  it('navigates enabled menu items, closes on Escape with focus restored and on outside click', () => {
    const pick = vi.fn();
    render(
      <>
        <Menu
          items={[
            { label: 'Suspend', onClick: pick },
            { label: 'Resume', disabled: true, onClick: pick },
            { separator: true },
            { label: 'End', tone: 'negative', onClick: pick },
          ]}
        />
        <button>Outside</button>
      </>,
    );
    const trigger = screen.getByRole('button', { name: 'Row actions' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Suspend' }));
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'End' }));
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'Suspend' }));
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('menuitem', { name: 'End' }));
    expect(pick).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole('button', { name: 'Outside' }));
    expect(screen.queryByRole('menu')).toBeNull();
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
  });

  it('keeps a row menu from activating the row and renders the structured range', () => {
    const row = vi.fn();
    const pick = vi.fn();
    render(
      <>
        <TableRow
          columns={[{ key: 'actions', label: '', width: '52px', kind: 'actions' }]}
          cells={{}}
          onClick={row}
          actions={[{ label: 'Edit', onClick: pick }]}
        />
        <TableFooter range={{ from: 1, to: 50, total: 1284 }} right={<button>Next</button>} />
      </>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Row actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }));
    expect(row).not.toHaveBeenCalled();
    expect(pick).toHaveBeenCalledOnce();
    expect(screen.getByText('1–50').className).toBe('range');
    expect(screen.getByText('1–50').parentElement?.textContent).toBe('1–50 of 1,284');
  });
});
