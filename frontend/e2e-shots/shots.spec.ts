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
      await page.screenshot({ path: `/web/.shots/${name}-${width}.png`, fullPage: true });
    }
  }
});
