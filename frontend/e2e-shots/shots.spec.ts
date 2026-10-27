import { expect, test } from '@playwright/test';
import { signIn } from '../e2e/auth';

test('capture the admin pages at both design widths', async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto('/subscribers');
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Subscribers', exact: true })).toBeVisible();
  for (const width of [1280, 1600]) {
    await page.setViewportSize({ width, height: width === 1280 ? 900 : 1000 });
    for (const name of [
      'subscribers',
      'card-prepaid',
      'card-postpaid',
      'catalog',
      'billing-runs',
      'orders',
      'products',
      'numbers',
      'bills',
      'payments',
      'balances',
      'prepaid-commands',
      'financial-accounts',
      'billing-settings',
      'parties',
      'integrations',
      'ui-kit',
    ]) {
      await page.goto(name.startsWith('card-') ? '/subscribers' : `/${name}`);
      await page.waitForLoadState('networkidle');
      if (name.startsWith('card-')) {
        await page
          .getByRole('group', { name: 'Account mode' })
          .getByRole('button', { name: name === 'card-prepaid' ? /^Prepaid/ : /^Postpaid/ })
          .click();
        await page
          .locator('[data-ui="ui.subscribers.list"] [role="row"].clickable')
          .first()
          .getByRole('cell')
          .first()
          .click();
        await expect(page.locator('[data-ui="ui.subscriber.card"] h1')).toBeVisible();
        await expect(page.getByTestId('skeleton')).toHaveCount(0);
        await page.waitForLoadState('networkidle');
      }
      if (
        name === 'ui-kit' &&
        !(await page.getByRole('heading', { name: 'UI kit', exact: true }).count())
      )
        continue;
      if (name === 'ui-kit') {
        const primaries = page.locator('.ui-kit-write-states .bss-write-actions .primary');
        const idle = await primaries.nth(0).boundingBox();
        const busy = await primaries.nth(1).boundingBox();
        expect(idle?.width).toBe(busy?.width);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      await page.screenshot({ path: `/web/.shots/${name}-${width}.png`, fullPage: true });
      if (name.startsWith('card-')) {
        for (const tab of [
          'Bills',
          'Payments',
          'Charges',
          'Transactions',
          ...(name === 'card-prepaid' ? ['Balance actions', 'Commands'] : []),
          'Account',
        ]) {
          await page.getByRole('tab', { name: new RegExp(`^${tab}`) }).click();
          await expect(page.getByRole('tabpanel')).toBeVisible();
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
          await page.screenshot({
            path: `/web/.shots/${name}-${tab.toLowerCase().replace(' ', '-')}-${width}.png`,
            fullPage: true,
          });
        }
      }
    }
  }
});
