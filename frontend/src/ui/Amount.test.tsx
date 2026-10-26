import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Amount } from './Amount';

describe('Ledger Amount', () => {
  it('renders U+2212 negatives, separate currency and neutral signed positive figures', () => {
    const { rerender } = render(<Amount cents={-4210} currency="EUR" />);
    expect(screen.getByText('−42.10').parentElement?.className).toBe('bss-amount negative');
    expect(screen.getByText('EUR').className).toBe('currency');
    rerender(<Amount cents={1000} signed />);
    expect(screen.getByText('+10.00').parentElement?.className).toBe('bss-amount');
    rerender(<Amount cents={1000} />);
    expect(screen.getByText('10.00').parentElement?.className).toBe('bss-amount');
    rerender(<Amount cents={0} signed />);
    expect(screen.getByText('0.00').parentElement?.className).toBe('bss-amount');
  });
});
