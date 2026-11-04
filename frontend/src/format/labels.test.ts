import { describe, expect, it } from 'vitest';
import { agoLabel, eventTypeLabel, paymentMethodLabel, plural, runTriggerLabel } from './labels';

describe('plural', () => {
  it('uses the singular only for one', () => {
    expect(plural(0, 'product')).toBe('0 products');
    expect(plural(1, 'product')).toBe('1 product');
    expect(plural(2, 'product')).toBe('2 products');
    expect(plural(3, 'day')).toBe('3 days');
    expect(plural(2, 'currency')).toBe('2 currencies');
    expect(plural(1, 'account')).toBe('1 account');
  });
});

describe('labels', () => {
  it('names a payment method, passing unknown codes through', () => {
    expect(paymentMethodLabel('bankTransfer')).toBe('Bank transfer');
    expect(paymentMethodLabel('card')).toBe('Card');
    expect(paymentMethodLabel('Crypto')).toBe('Crypto');
  });
  it('names a run trigger', () => {
    expect(runTriggerLabel('onCycle')).toBe('Scheduled');
    expect(runTriggerLabel('offCycle')).toBe('Off-cycle');
    expect(runTriggerLabel('x')).toBe('x');
  });
  it('names an event type', () => {
    expect(eventTypeLabel('CustomerBill')).toBe('Bill');
    expect(eventTypeLabel('TMF654 balanceActionHistory')).toBe('Balance action history');
    expect(eventTypeLabel('CustomerBillStateChangeEvent')).toBe('Bill state change event');
    expect(eventTypeLabel('Other')).toBe('Other');
  });
});

describe('agoLabel', () => {
  it('reads minutes, hours and days', () => {
    const now = '2026-10-28T12:00:00Z';
    expect(agoLabel('2026-10-28T11:30:00Z', now)).toBe('30 min ago');
    expect(agoLabel('2026-10-27T20:00:00Z', now)).toBe('16 h ago');
    expect(agoLabel('2026-10-25T12:00:00Z', now)).toBe('3 days ago');
  });
});
