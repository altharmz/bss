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
      'users',
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
      if (name === 'users' || name === 'financial-accounts') {
        const user = name === 'users';
        await page
          .getByRole('button', { name: user ? 'New user' : '+ New financial account', exact: true })
          .click();
        const dialog = page.getByRole('dialog');
        const primary = dialog.locator('.bss-modal-footer .primary');
        await expect(primary).toBeInViewport({ ratio: 1 });
        await page.screenshot({ path: `/web/.shots/dialog-${name}-${width}.png` });
        await dialog.getByRole('button', { name: 'Cancel' }).click();
      }
      if (name === 'card-postpaid') {
        await page.getByRole('button', { name: 'Edit', exact: true }).click();
        const edit = page.getByRole('dialog', { name: 'Edit subscriber' });
        await expect(edit.getByLabel('Account name')).not.toHaveValue('');
        await expect(edit.getByRole('button', { name: 'Save', exact: true })).toBeInViewport({
          ratio: 1,
        });
        await page.screenshot({ path: `/web/.shots/dialog-edit-${width}.png` });
        await edit.getByRole('button', { name: 'Cancel' }).click();
        await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
        const add = page.getByRole('dialog', { name: 'Add product' });
        await expect(add.getByRole('radio').first()).toBeVisible();
        await expect(add.getByRole('button', { name: 'Activate', exact: true })).toBeInViewport({
          ratio: 1,
        });
        await page.screenshot({ path: `/web/.shots/dialog-add-${width}.png` });
        await add.getByRole('button', { name: 'Cancel' }).click();
      }
      if (name === 'catalog') {
        await page.getByRole('button', { name: '+ New offering' }).click();
        const offering = page.getByRole('dialog', { name: 'New product offering' });
        await expect(offering.locator('.bss-modal-footer .primary')).toBeInViewport({ ratio: 1 });
        await page.screenshot({ path: `/web/.shots/dialog-offering-${width}.png` });
        await offering.getByRole('button', { name: 'Cancel' }).click();
        await page.getByRole('button', { name: 'Categories', exact: true }).click();
        await page.getByRole('button', { name: '+ New category' }).click();
        const category = page.getByRole('dialog', { name: 'New category' });
        await expect(category.getByRole('button', { name: 'Create', exact: true })).toBeInViewport({
          ratio: 1,
        });
        await page.screenshot({ path: `/web/.shots/dialog-category-${width}.png` });
        await category.getByRole('button', { name: 'Cancel' }).click();
      }
      if (name.startsWith('card-')) {
        const actions = page.locator('[data-ui="ui.subscriber.productActions"]').first();
        await actions.click();
        const menu = page.getByRole('menu');
        await expect(menu).toBeVisible();
        expect(await menu.evaluate((element) => element.parentElement === document.body)).toBe(
          true,
        );
        const items = menu.getByRole('menuitem');
        for (const item of await items.all()) await expect(item).toBeInViewport();
        // Full-page capture can resize the viewport, which intentionally dismisses Menu.
        await page.screenshot({ path: `/web/.shots/${name}-menu-${width}.png` });
        await expect(menu).toBeVisible();
        await actions.press('Escape');
        if (name === 'card-postpaid') {
          await page.route(
            '**/tmf-api/productOrderingManagement/v5/productOrder',
            async (route) => {
              if (route.request().method() !== 'POST') return route.continue();
              await route.fulfill({
                status: 201,
                contentType: 'application/json',
                body: JSON.stringify({
                  id: 'preview-refusal',
                  state: 'rejected',
                  productOrderErrorMessage: [
                    { code: 'openModifyItem', reason: 'another order changes this product' },
                  ],
                }),
              });
            },
          );
          await actions.click();
          await page.getByRole('menuitem', { name: 'End on a date…' }).click();
          const refused = page.getByRole('dialog');
          await refused.getByRole('button', { name: 'End on a date', exact: true }).click();
          await expect(refused.getByRole('alert')).toContainText('Refused.');
          await expect(refused.getByRole('alert')).toContainText('openModifyItem');
          await expect(
            refused.getByRole('button', { name: 'End on a date', exact: true }),
          ).toBeInViewport({ ratio: 1 });
          await page.screenshot({ path: `/web/.shots/dialog-refused-order-${width}.png` });
          await refused.getByRole('button', { name: 'Cancel', exact: true }).click();
          await page.unroute('**/tmf-api/productOrderingManagement/v5/productOrder');
        }
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
