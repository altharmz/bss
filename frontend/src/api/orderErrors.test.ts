import { describe, expect, it } from 'vitest';
import codes from './orderErrors.codes.json';
import { ORDER_ERRORS, orderRefusal } from './orderErrors';

describe('order refusals', () => {
  it('exports exactly the mapped codes for backend parity', () => {
    expect(Object.keys(ORDER_ERRORS).sort()).toEqual(codes);
  });
  it.each(codes)('maps %s to an operator sentence', (code) => {
    const refusal = orderRefusal([{ code, reason: 'raw server text' }]);
    expect(refusal.problem).toBe(ORDER_ERRORS[code]);
    expect(refusal.problem).not.toBe('raw server text');
    expect(refusal.code).toBe(code);
  });
  it('retains the reason for unknown or absent codes', () => {
    expect(orderRefusal([{ code: 'newCode', reason: 'A future refusal' }])).toEqual({
      problem: 'A future refusal',
      code: 'newCode',
    });
    expect(orderRefusal([{ reason: 'Refused by the server' }]).problem).toBe(
      'Refused by the server',
    );
  });
});
