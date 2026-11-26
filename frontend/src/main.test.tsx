import { act, screen } from '@testing-library/react';
import type { Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { stubFetch, tmfError } from './test/fetch';

// The admin entry point (main.tsx) renders the app into #root as soon as index.html loads
// it: these tests import the real module. The roots it creates are recorded to unmount them.
const roots = vi.hoisted(() => [] as Root[]);
vi.mock('react-dom/client', async (load) => {
  const actual = await load<typeof import('react-dom/client')>();
  return {
    ...actual,
    createRoot: (...args: Parameters<typeof actual.createRoot>) => {
      const root = actual.createRoot(...args);
      roots.push(root);
      return root;
    },
  };
});

afterEach(() => {
  act(() => roots.splice(0).forEach((root) => root.unmount()));
  document.getElementById('root')?.remove();
});

describe('admin entry point', () => {
  it('refuses a page without #root, rendering nothing', async () => {
    vi.resetModules();
    await expect(import('./main')).rejects.toThrow('index.html has no #root');
    expect(roots).toEqual([]);
  });

  it('renders the app into #root: a signed-out operator gets the sign-in form', async () => {
    stubFetch({ 'GET /internal/v1/session': () => tmfError(401, 'Unauthorized') });
    const root = document.createElement('div');
    root.id = 'root';
    document.body.append(root);
    window.history.replaceState(null, '', '/catalog');
    vi.resetModules();
    await act(async () => {
      await import('./main');
    });
    expect(roots).toHaveLength(1);
    const heading = await screen.findByRole('heading', { name: 'Sign in' });
    expect(root.contains(heading)).toBe(true);
    expect(window.location.pathname).toBe('/sign-in');
  });
});
