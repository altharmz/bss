import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { Combobox } from './combobox';
import { Field } from './kit';

const OPTIONS = [
  { value: 'p1', label: 'Olga Kuznetsova · person' },
  { value: 'o1', label: 'Romashka LLC · organization' },
  { value: 'o2', label: 'Lone Ltd · organization' },
];

function Example() {
  const [value, setValue] = useState('');
  return (
    <Field label="Owner">
      <Combobox options={OPTIONS} value={value} onChange={setValue} placeholder="Choose a party" />
    </Field>
  );
}

describe('Combobox', () => {
  it('filters as you type and selects with the keyboard', () => {
    render(<Example />);
    const input = screen.getByRole('combobox', { name: 'Owner' });
    fireEvent.focus(input);
    expect(screen.getByRole('listbox')).toBeTruthy();
    expect(screen.getAllByRole('option')).toHaveLength(3);
    fireEvent.change(input, { target: { value: 'rom' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option', { name: 'Romashka LLC · organization' })).toBeTruthy();
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect((input as HTMLInputElement).value).toBe('Romashka LLC · organization');
    expect(screen.queryByRole('listbox')).toBeNull();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'missing' } });
    expect(screen.getByText('No matches')).toBeTruthy();
  });
});
