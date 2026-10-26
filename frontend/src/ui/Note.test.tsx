import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Note } from './kit';

describe('Note', () => {
  it.each(['info', 'warning', 'negative'] as const)(
    'renders %s with its verdict, glyph and code',
    (tone) => {
      render(
        <Note tone={tone} lead="Verdict." code="SERVER_CODE">
          Server message
        </Note>,
      );
      const note = screen.getByRole(tone === 'negative' ? 'alert' : 'note');
      expect(note.classList.contains(`bss-note-${tone}`)).toBe(true);
      expect(within(note).getByText('Verdict.').tagName).toBe('STRONG');
      expect(note.textContent).toContain('Server message');
      expect(within(note).getByText('SERVER_CODE').className).toBe('bss-note-code');
      expect(note.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
    },
  );
});
