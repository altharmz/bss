import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CONTEXT } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { operatorInitials, TopBar } from './Shell';

describe('Ledger shell', () => {
  it.each([
    ['2026-10-02', null],
    ['2026-10-01', '1 day behind the wall clock'],
    ['2026-09-29', '3 days behind the wall clock'],
    ['2026-10-03', '1 day ahead of the wall clock'],
  ])('compares business date %s with the context instant in its zone', (day, drift) => {
    render(
      <TopBar
        context={{
          ...CONTEXT,
          now: '2026-10-01T23:00:00Z',
          timeZone: 'Asia/Kolkata',
          utcOffset: '+05:30',
          businessDate: day,
        }}
      />,
    );
    const chip = expectUi('ui.shell.businessDate');
    expect(chip.classList.contains('is-warning')).toBe(drift !== null);
    expect(screen.getByText(day)).toBeTruthy();
    expect(screen.getByText('UTC+5:30')).toBeTruthy();
    if (drift) expect(screen.getByText(drift)).toBeTruthy();
    else expect(chip.querySelector('.drift')).toBeNull();
  });

  it.each([
    ['i.petrova', undefined, 'I.'],
    ['ekask', 'Elena Kask', 'EK'],
    ['ekask', '  Elena   Kask Other  ', 'EK'],
    ['ekask', 'Elena', 'EK'],
    ['a', undefined, 'A'],
  ])('derives initials for login %s and display name %s', (login, name, initials) => {
    expect(operatorInitials(login, name)).toBe(initials);
  });
});
