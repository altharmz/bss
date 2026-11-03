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
      if (name === 'orders') {
        const first = page.locator('[data-ui="ui.orders"] [role="row"].clickable').first();
        if (await first.count()) {
          await first.click();
          const dialog = page.getByRole('dialog');
          await expect(dialog.locator('[data-ui="ui.orders.items"]')).toBeVisible();
          await page.waitForLoadState('networkidle');
          await expect(dialog.getByText('Target status', { exact: true })).toBeVisible();
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
          expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
          await page.screenshot({ path: `/web/.shots/dialog-order-${width}.png` });
          await dialog.getByRole('button', { name: 'Edit details', exact: true }).click();
          await expect(
            dialog.getByRole('button', { name: 'Save details', exact: true }),
          ).toBeInViewport({ ratio: 1 });
          await page.screenshot({ path: `/web/.shots/dialog-order-details-${width}.png` });
          await dialog
            .locator('footer')
            .getByRole('button', { name: 'Close', exact: true })
            .click();
        }
      }
      if (name === 'billing-runs') {
        const latest = page.getByRole('region', { name: 'Latest run', exact: true });
        if (await latest.count()) {
          await latest.getByRole('button', { name: 'Accounts', exact: true }).click();
          const outcomes = page.getByRole('region', { name: /^Run for / });
          await expect(outcomes).toBeVisible();
          await expect(outcomes.getByLabel('Loading', { exact: true })).toHaveCount(0);
          await page.waitForLoadState('networkidle');
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
          await page.screenshot({
            path: `/web/.shots/billing-runs-outcomes-${width}.png`,
            fullPage: true,
          });
        }
      }
      if (name === 'subscribers') {
        await page.getByRole('button', { name: 'New subscriber', exact: true }).click();
        await page.screenshot({ path: `/web/.shots/dialog-new-subscriber-${width}.png` });
        await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
      }
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
        await expect(add.getByRole('button', { name: 'Add product', exact: true })).toBeInViewport({
          ratio: 1,
        });
        await add.getByRole('radio').first().click();
        await expect(add.getByTestId('first-charge')).toBeVisible();
        await expect(add.locator('.bss-modal-footer .primary')).toBeInViewport({ ratio: 1 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        await page.screenshot({ path: `/web/.shots/dialog-add-${width}.png` });
        await add.getByRole('button', { name: 'Cancel' }).click();
      }
      if (name === 'card-prepaid') {
        await page.route('**/firstCharge?*', async (route) => {
          const start = new URL(route.request().url()).searchParams.get('startDate');
          const money = (value: number) => ({ unit: 'EUR', value });
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              startDate: start,
              period: { start, end: '2026-11-15' },
              cyclePeriod: { start: '2026-10-15', end: '2026-11-15' },
              billableDays: 20,
              periodDays: 31,
              prorated: true,
              price: money(24.9),
              amount: money(16.06),
              chargeRunDate: start,
              billDate: '2026-11-15',
              next: { start: '2026-11-15', amount: money(24.9) },
              balance: money(13.5),
              balanceAfter: money(-2.56),
              creditLimit: null,
              exceedsCreditLimit: false,
              required: money(16.06),
              available: money(13.5),
              shortfall: money(2.56),
            }),
          });
        });
        await page.getByRole('button', { name: 'Add product', exact: true }).first().click();
        const add = page.getByRole('dialog', { name: 'Add product' });
        await add.getByRole('radio').first().click();
        await expect(add.getByTestId('prepaid-required')).toHaveText('16.06 EUR');
        await expect(
          add.getByRole('note').filter({ hasText: 'Shortfall 2.56 EUR.' }),
        ).toBeVisible();
        await expect(add.getByRole('button', { name: 'Top up 2.56 EUR…' })).toBeInViewport({
          ratio: 1,
        });
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        await page.screenshot({ path: `/web/.shots/dialog-add-prepaid-${width}.png` });
        await add.getByRole('button', { name: 'Cancel' }).click();
        await page.unroute('**/firstCharge?*');
      }
      if (name === 'catalog') {
        await page.locator('.catalog-offerings .clickable').first().click();
        const detail = page.getByRole('dialog');
        await expect(detail.getByRole('list', { name: 'Lifecycle' })).toBeVisible();
        await page.waitForLoadState('networkidle');
        await page.screenshot({ path: `/web/.shots/catalog-detail-${width}.png` });
        await detail.getByRole('button', { name: 'Offering actions' }).click();
        await expect(page.getByRole('menuitem', { name: 'Edit offering' })).toBeVisible();
        await page.screenshot({ path: `/web/.shots/catalog-detail-menu-${width}.png` });
        await detail.getByRole('button', { name: 'Offering actions' }).press('Escape');
        await detail.getByRole('button', { name: 'Close', exact: true }).last().click();
        for (const tab of ['Specifications', 'Categories', 'Catalogs', 'Import / export']) {
          await page.getByRole('tab', { name: tab, exact: true }).click();
          await page.waitForLoadState('networkidle');
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
          await page.screenshot({
            path: `/web/.shots/catalog-${tab.toLowerCase().replaceAll(' ', '').replace('/', '-')}-${width}.png`,
            fullPage: true,
          });
        }
        await page.getByRole('tab', { name: 'Offerings', exact: true }).click();
        await page.getByRole('button', { name: '+ New offering' }).click();
        const offering = page.getByRole('dialog', { name: 'New product offering' });
        await expect(offering.locator('.bss-modal-footer .primary')).toBeInViewport({ ratio: 1 });
        await page.screenshot({ path: `/web/.shots/dialog-offering-${width}.png` });
        await offering.getByRole('button', { name: 'Cancel' }).click();
        await page.getByRole('tab', { name: 'Categories', exact: true }).click();
        await page.getByRole('button', { name: '+ New category' }).click();
        const category = page.getByRole('dialog', { name: 'New category' });
        await expect(category.getByRole('button', { name: 'Create', exact: true })).toBeInViewport({
          ratio: 1,
        });
        await page.screenshot({ path: `/web/.shots/dialog-category-${width}.png` });
        await category.getByRole('button', { name: 'Cancel' }).click();
        await page.getByRole('tab', { name: 'Specifications', exact: true }).click();
        await page.getByRole('button', { name: '+ New specification' }).click();
        const specification = page.getByRole('dialog', { name: 'New product specification' });
        await specification.getByLabel('Name', { exact: true }).fill('Numbered mobile');
        await specification.getByLabel('Requires a subscriber number').check();
        await specification.getByRole('button', { name: 'Add characteristic' }).click();
        await specification.getByLabel('Characteristic name', { exact: true }).fill('Color');
        await specification.getByLabel('Values', { exact: true }).fill('blue, green');
        await expect(
          specification.getByRole('button', { name: 'Create', exact: true }),
        ).toBeInViewport({ ratio: 1 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        await page.screenshot({ path: `/web/.shots/dialog-specification-${width}.png` });
        await specification.getByRole('button', { name: 'Cancel' }).click();
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
        await actions.click();
        await page.getByRole('menuitem', { name: 'Subscriber number', exact: true }).click();
        const number = page.getByRole('dialog', { name: 'Subscriber number' });
        await expect(
          number.getByText(/This product's offering does not use subscriber numbers/),
        ).toBeVisible();
        await page.screenshot({ path: `/web/.shots/dialog-product-number-${name}-${width}.png` });
        await number.getByRole('button', { name: 'Close', exact: true }).last().click();
        if (name === 'card-prepaid') {
          for (const [action, title] of [
            ['Top up', 'Top up'],
            ['Adjust', 'Adjust balance'],
            ['Hold', 'Hold funds'],
          ] as const) {
            await page.getByRole('button', { name: action, exact: true }).click();
            const money = page.getByRole('dialog', { name: title, exact: true });
            await money.getByLabel('Amount', { exact: true }).fill('5.00');
            if (action === 'Top up')
              await expect(money.getByLabel('Number', { exact: true })).toBeEnabled();
            if (action === 'Adjust')
              await money.getByLabel('Reason', { exact: true }).fill('Correction');
            await page.screenshot({
              path: `/web/.shots/dialog-money-${action.toLowerCase().replace(' ', '-')}-${width}.png`,
            });
            await money.getByRole('button', { name: 'Cancel' }).click();
          }
          await page.getByRole('button', { name: 'Transfer', exact: true }).click();
          const transfer = page.getByRole('dialog', { name: 'Transfer funds' });
          await expect(
            transfer.getByText(/This account has no active product with a subscriber number/),
          ).toBeVisible();
          await page.screenshot({ path: `/web/.shots/dialog-transfer-${width}.png` });
          await transfer.getByRole('button', { name: 'Cancel' }).click();

          // Read-only fixture for all three transfer steps; never send a money write.
          const senderId = new URL(page.url()).pathname.split('/').pop();
          const source = await (
            await page.request.get(`/tmf-api/accountManagement/v5/billingAccount/${senderId}`)
          ).json();
          const receiverId = 'f0000002-0000-4000-8000-000000000002';
          const fixture = async (route: import('@playwright/test').Route) => {
            const url = new URL(route.request().url());
            let body;
            if (url.pathname.endsWith('/product') && url.searchParams.has('billingAccount.id'))
              body = [{ id: 'fixture-product', realizingResource: [{ id: 'fixture-sender' }] }];
            else if (url.pathname.endsWith('/resource/fixture-sender'))
              body = { id: 'fixture-sender', value: '79991234000' };
            else if (
              url.pathname.endsWith('/resource') &&
              url.searchParams.get('value') === '79991234001'
            )
              body = [{ id: 'fixture-receiver', value: '79991234001' }];
            else if (
              url.pathname.endsWith('/product') &&
              url.searchParams.has('realizingResource.id')
            )
              body = [
                { id: 'fixture-product-rx', status: 'active', billingAccount: { id: receiverId } },
              ];
            else if (url.pathname.endsWith(`/billingAccount/${receiverId}`))
              body = {
                ...source,
                id: receiverId,
                name: 'Anna Kask',
                relatedParty: [
                  {
                    role: 'customer',
                    partyOrPartyRole: {
                      id: 'fixture-customer',
                      name: 'Anna Kask',
                      '@type': 'PartyRoleRef',
                    },
                    '@type': 'RelatedPartyRefOrPartyRoleRef',
                  },
                ],
              };
            else if (
              url.pathname.endsWith('/bucket') &&
              url.searchParams.get('partyAccount.id') === receiverId
            )
              body = [
                {
                  id: 'fixture-bucket',
                  remainingValue: { amount: 3.2, units: source.currency },
                  reservedValue: { amount: 0, units: source.currency },
                },
              ];
            else return route.continue();
            await route.fulfill({
              json: body,
              headers: Array.isArray(body) ? { 'X-Total-Count': String(body.length) } : {},
            });
          };
          await page.route('**/tmf-api/**', fixture);
          await page.getByRole('button', { name: 'Transfer', exact: true }).click();
          await expect(transfer.getByRole('button', { name: '79991234000' })).toBeVisible();
          await transfer.getByLabel('Receiver number').fill('79991234001');
          await transfer.getByRole('button', { name: 'Find', exact: true }).click();
          await expect(transfer.locator('.money-recipient')).toContainText('Anna Kask');
          await page.screenshot({ path: `/web/.shots/dialog-transfer-recipient-${width}.png` });
          await transfer.getByRole('button', { name: 'Next', exact: true }).click();
          await transfer.getByLabel('Amount', { exact: true }).fill('5.00');
          await transfer
            .getByLabel('Reason', { exact: true })
            .fill('Subscriber requested transfer');
          await page.screenshot({ path: `/web/.shots/dialog-transfer-amount-${width}.png` });
          await transfer.getByRole('button', { name: 'Review transfer' }).click();
          await transfer.getByRole('checkbox').check();
          await page.screenshot({ path: `/web/.shots/dialog-transfer-confirm-${width}.png` });
          await transfer.getByRole('button', { name: 'Cancel' }).click();
          await page.unroute('**/tmf-api/**', fixture);
        }

        if (name === 'card-postpaid') {
          await page.getByRole('button', { name: 'More actions', exact: true }).click();
          await page.getByRole('menuitem', { name: 'Record payment', exact: true }).click();
          const payment = page.getByRole('dialog', { name: 'Record payment' });
          await payment.getByLabel('Amount received').fill('42.10');
          await payment.getByLabel('External reference').fill('BANK-77');
          await payment.getByLabel('Payment description').fill('Cash at the desk');
          await page.screenshot({ path: `/web/.shots/dialog-record-payment-${width}.png` });
          await payment.getByRole('button', { name: 'Cancel' }).click();
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
          if (name === 'card-postpaid' && tab === 'Payments') {
            await page.locator('[data-ui="ui.subscriber.payments.recordRefund"]').click();
            const refund = page.getByRole('dialog', { name: 'Record refund' });
            await refund.getByLabel('Refund description').fill('Returned deposit');
            await page.screenshot({ path: `/web/.shots/dialog-record-refund-${width}.png` });
            await refund.getByRole('button', { name: 'Close', exact: true }).last().click();
            await page.locator('[data-ui="ui.subscriber.payments"] .clickable').first().click();
            const detail = page.getByRole('dialog');
            await expect(detail.getByRole('button', { name: 'Record refund' })).toBeVisible();
            await page.screenshot({ path: `/web/.shots/dialog-payment-detail-${width}.png` });
            await detail.getByRole('button', { name: 'Close', exact: true }).last().click();
          }
        }
      }
    }
  }
});
