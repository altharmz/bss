import { describe, expect, it } from 'vitest';
import { accountLabel, matchesShortId, shortId } from './ids';

const ID = '1f3a9c2b-0000-4000-8000-000000000001';

describe('display ids', () => {
  it('is the first 8 hex digits, upper case', () => {
    expect(shortId(ID)).toBe('1F3A9C2B');
    expect(accountLabel(ID)).toBe('BA-1F3A9C2B');
  });

  it('refuses anything that is not a UUID', () => {
    expect(() => shortId('1f3a9c2b')).toThrow(RangeError);
  });

  it.each([
    ['3a9c', true],
    ['1F3A 9C2B', true],
    ['1f3a9c2b', true],
    ['0000', false], // only the displayed part is searched
    ['', false],
    ['  ', false],
  ])('matchesShortId(%j) = %s', (query, hit) => {
    expect(matchesShortId(query, ID)).toBe(hit);
  });
});
