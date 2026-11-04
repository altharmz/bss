// Theme choice (decision D3): System / Light / Dark, kept in localStorage under
// `bss.theme` and applied as data-theme on <html>. System removes the attribute, so the
// prefers-color-scheme block in tokens.css decides. Every storage access is guarded:
// a blocked or throwing localStorage falls back to System.

export type Theme = 'system' | 'light' | 'dark';

export const THEME_KEY = 'bss.theme';

export function readTheme(): Theme {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
}

export function saveTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    if (theme === 'system') window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // The choice still applies to this page; it is just not remembered.
  }
}
