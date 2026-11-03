import { expect, test, type Locator } from '@playwright/test';
import { signIn } from './auth';

for (const viewport of [
  { width: 1366, height: 768 },
  { width: 1920, height: 900 },
]) {
  test(`dialog primaries stay visible at ${viewport.width}×${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await page.goto('/subscribers');
    await signIn(page);
    await page.getByRole('row').filter({ hasText: 'Olga Kuznetsova' }).click();
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    const edit = page.getByRole('dialog', { name: 'Edit subscriber' });
    await expect(edit.getByLabel('Account name')).not.toHaveValue('');
    await expect(edit.getByRole('group', { name: 'Presentation media' })).toBeAttached();
    const check = async (dialog: Locator, primary: Locator) => {
      await expect(primary).toBeInViewport({ ratio: 1 });
      const body = dialog.locator('.bss-dialog-body');
      await body.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      await expect(primary).toBeInViewport({ ratio: 1 });
      await expect(dialog).toHaveJSProperty('scrollTop', 0);
    };
    await check(edit, edit.getByRole('button', { name: 'Save', exact: true }));
    await edit.getByRole('button', { name: 'Cancel' }).click();
    await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
    const add = page.getByRole('dialog', { name: 'Add product' });
    await check(add, add.getByRole('button', { name: 'Add product', exact: true }));
    await add.getByRole('button', { name: 'Cancel' }).click();
    await page.goto('/catalog');
    await page.getByRole('button', { name: '+ New offering' }).click();
    const offering = page.getByRole('dialog', { name: 'New product offering' });
    await check(offering, offering.locator('.bss-modal-footer .bss-btn.primary'));
    await offering.getByRole('button', { name: 'Cancel' }).click();
    await page.getByRole('tab', { name: 'Categories', exact: true }).click();
    await page.getByRole('button', { name: '+ New category' }).click();
    const category = page.getByRole('dialog', { name: 'New category' });
    await check(category, category.getByRole('button', { name: 'Create', exact: true }));
    await category.getByRole('button', { name: 'Cancel' }).click();
  });
}
