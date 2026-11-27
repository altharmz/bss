import { describe, expect, it } from 'vitest';
import { checkOffering, type OfferingForm } from './newOffering';

const TODAY = '2026-10-01';
const OK: OfferingForm = {
  code: 'mob-xl',
  name: 'Mobile XL',
  description: '',
  isSellable: true,
  categoryIds: [],
  specificationId: 's1',
  price: '1,990.5',
  validFrom: '2026-10-02',
  validTo: '',
};
const check = (over: Partial<OfferingForm>, taken: string[] = ['MOB-L']) =>
  checkOffering({ ...OK, ...over }, new Set(taken), TODAY);

describe('New offering validation', () => {
  it('accepts a complete form, upper-casing the code and canonicalising the price', () => {
    expect(check({})).toEqual({ error: null, ready: true, code: 'MOB-XL', amount: '1990.50' });
  });

  it.each([
    [{ code: 'mob-l' }, 'Code MOB-L is already in use'],
    [{ code: 'MOB L' }, 'Code may use A-Z, 0-9, "-" and "_" (up to 32 characters)'],
    [{ code: '-X' }, 'Code may use A-Z, 0-9, "-" and "_" (up to 32 characters)'],
    [{ price: '0' }, 'Price must be a positive number'],
    [{ price: '-5' }, 'Price must be a positive number'],
    [{ price: '12.345' }, 'Price must be a positive number'],
    [{ price: 'abc' }, 'Price must be a positive number'],
    [{ recurringPeriodType: 'hourly' }, 'Choose a supported charge period'],
    [{ recurringPeriodLength: '0' }, 'Charge period length must be a whole number from 1 to 1000'],
    [
      { recurringPeriodLength: '1.5' },
      'Charge period length must be a whole number from 1 to 1000',
    ],
    [
      { recurringPeriodLength: '1001' },
      'Charge period length must be a whole number from 1 to 1000',
    ],
    [{ validFrom: '2026-10-01' }, 'Valid from cannot be earlier than 2026-10-02'],
    [{ validTo: '2026-10-01', validFrom: '2026-10-05' }, 'End date is before start date'],
  ])('%j -> %s', (over, message) => {
    const result = check(over);
    expect(result.error).toBe(message);
    expect(result.ready).toBe(false);
  });

  it('is not ready until code, name, specification, a valid price and valid from are filled', () => {
    for (const field of ['code', 'name', 'specificationId', 'price', 'validFrom'] as const) {
      const result = check({ [field]: '' });
      expect(result.ready, field).toBe(false);
      expect(result.error, field).toBeNull(); // empty is not an error, just not ready
    }
  });

  it('accepts an end on the start day (one valid day)', () => {
    expect(check({ validTo: '2026-10-02' }).ready).toBe(true);
  });
});
