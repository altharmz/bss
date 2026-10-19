import { readFileSync } from 'node:fs';
import { expect, type Page } from '@playwright/test';

export function identity(role: 'admin' | 'user') {
  const credentials = JSON.parse(readFileSync('/web/.e2e/credentials.json', 'utf8')) as Record<
    'admin' | 'user',
    { username: string; password: string }
  >;
  return credentials[role];
}

export async function signIn(page: Page, role: 'admin' | 'user' = 'admin') {
  const user = identity(role);
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.getByLabel('Username').fill(user.username);
  await page.getByLabel('Password', { exact: true }).fill(user.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}
