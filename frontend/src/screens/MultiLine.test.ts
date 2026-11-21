import { describe, expect, it } from 'vitest';
import { multiLineLabel, rateDiscount } from './MultiLine';

describe('multi-line discount text (#380)', () => {
  it('words a discount as the bill document does', () => {
    expect(multiLineLabel({ value: 5, unit: 'USD' })).toBe('Multi-line −$5.00');
    expect(multiLineLabel({ value: 1250.5, unit: 'EUR' })).toBe('Multi-line −€1,250.50');
    expect(multiLineLabel({ value: 3, unit: 'CHF' })).toBe('Multi-line −3.00 CHF');
  });

  it('reads the discount of a TMF678 rate description', () => {
    const id = '0d000000-0000-4000-8000-000000000001';
    expect(
      rateDiscount(
        `Plan 30, 2026-10-01 - 2026-10-31; multi-line discount −$5.00 (productOfferingPrice ${id})`,
      ),
    ).toBe('Multi-line −$5.00');
    expect(
      rateDiscount(
        `Plan, 2026-10-01 - 2026-10-31; multi-line discount −3.00 CHF (productOfferingPrice ${id})`,
      ),
    ).toBe('Multi-line −3.00 CHF');
    expect(rateDiscount('Plan 30, 2026-10-01 - 2026-10-31')).toBeNull();
    expect(rateDiscount(undefined)).toBeNull();
  });
});
