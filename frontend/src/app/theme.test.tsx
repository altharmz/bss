import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ThemeChoice } from './Shell';
import { applyTheme, readTheme, saveTheme, THEME_KEY } from './theme';

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
});

describe('theme choice', () => {
  it('applies and removes data-theme and persists the choice', () => {
    render(<ThemeChoice />);
    const html = document.documentElement;
    expect(screen.getByRole('button', { name: 'System' }).getAttribute('aria-pressed')).toBe(
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Dark' }));
    expect(html.getAttribute('data-theme')).toBe('dark');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('dark');
    expect(screen.getByRole('button', { name: 'Dark' }).getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Light' }));
    expect(html.getAttribute('data-theme')).toBe('light');
    expect(window.localStorage.getItem(THEME_KEY)).toBe('light');

    fireEvent.click(screen.getByRole('button', { name: 'System' }));
    expect(html.hasAttribute('data-theme')).toBe(false);
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it('starts from the stored choice', () => {
    window.localStorage.setItem(THEME_KEY, 'dark');
    render(<ThemeChoice />);
    expect(screen.getByRole('button', { name: 'Dark' }).getAttribute('aria-pressed')).toBe('true');
    expect(readTheme()).toBe('dark');
    window.localStorage.setItem(THEME_KEY, 'nonsense');
    expect(readTheme()).toBe('system');
  });

  it('falls back to System without crashing when localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readTheme()).toBe('system');
    render(<ThemeChoice />);
    expect(screen.getByRole('button', { name: 'System' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Dark' }));
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(() => saveTheme('system')).not.toThrow();
    applyTheme('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});
