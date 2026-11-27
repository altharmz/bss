import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import type { Invoice } from '../api/wholesale';
import { RouterProvider, useRouter } from '../app/router';
import { json, stubFetch, tmfError } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { WholesaleInvoiceScreen, WholesaleScreen } from './Wholesale';

const ID = '30000000-0000-4000-8000-000000000001';
const PRODUCT = '30000000-0000-4000-8000-000000000002';
const ACCOUNT = '30000000-0000-4000-8000-000000000003';
const BASE = '/internal/v1/wholesale/invoice';
const DETAIL = `${BASE}/${ID}`;
const PAGE = 'offset=0&limit=25';
const UPLOAD = `${BASE}?invoiceNumber=Partner%2F2026-09`;
const CSV =
  'msisdn,iccid,period_start,period_end,charge_type,quantity,unit,amount,currency\r\n' +
  '12025550199,,2026-09-01,2026-10-01,line,,,1.20,USD\r\n';
const cost = { value: 1.2, unit: 'USD' };
const revenue = { value: 0, unit: 'USD' };
const margin = { value: -1.2, unit: 'USD' };
const invoice: Invoice = {
  id: ID,
  href: DETAIL,
  invoiceNumber: 'Partner/2026-09',
  period: { start: '2026-09-01', end: '2026-10-01' },
  currency: 'USD',
  total: cost,
  lineCount: 1,
  state: 'reconciled',
  importedAt: '2026-10-01T00:00:00Z',
  importedBy: 'operator',
  discrepancies: { billedNotActive: 1, activeNotBilled: 2, usageMismatch: 1, duplicateCharge: 0 },
  cost,
  revenue,
  margin,
  matchedProducts: 1,
  unmatchedNumbers: 1,
};

function RoutedScreen() {
  const { route } = useRouter();
  return route.name === 'wholesale' && route.invoiceId ? (
    <WholesaleInvoiceScreen id={route.invoiceId} />
  ) : (
    <WholesaleScreen />
  );
}

function renderWholesale(path = '/wholesale') {
  window.history.replaceState(null, '', path);
  render(
    <RouterProvider>
      <RoutedScreen />
    </RouterProvider>,
  );
}

function readBytes(file: File) {
  return new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

it('pins the raw CSV body and opens the imported invoice', async () => {
  const { fetchMock } = stubFetch({
    [`GET ${BASE}?${PAGE}`]: () => json([]),
    [`POST ${UPLOAD}`]: () => json(invoice, { status: 201 }),
    [`GET ${DETAIL}`]: () => json(invoice),
    [`GET ${DETAIL}/discrepancies?${PAGE}`]: () => json([]),
  });
  renderWholesale();
  expectUi('ui.wholesale');
  fireEvent.click(screen.getByRole('button', { name: 'Import invoice' }));
  const dialog = within(expectUi('ui.wholesale.import'));
  const submit = dialog.getByRole('button', { name: 'Import' });
  expect(submit.hasAttribute('disabled')).toBe(true);
  fireEvent.change(dialog.getByLabelText(/Invoice number/), {
    target: { value: invoice.invoiceNumber },
  });
  const file = new File([CSV], 'invoice.csv', { type: 'application/octet-stream' });
  fireEvent.change(dialog.getByLabelText('Invoice CSV'), { target: { files: [file] } });
  fireEvent.click(submit);

  await screen.findByRole('heading', { name: invoice.invoiceNumber });
  expect(window.location.pathname).toBe(`/wholesale/${ID}`);
  expect(screen.queryByRole('dialog')).toBeNull();
  const upload = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
  expect(upload?.[0]).toBe(UPLOAD);
  expect(upload?.[1]?.headers).toMatchObject({ 'Content-Type': 'text/csv', 'X-Bss-Request': '1' });
  expect(upload?.[1]?.body).toBe(file);
  expect(new TextDecoder().decode(await readBytes(file))).toBe(CSV);
});

it.each([
  [422, 'row 1, column amount'],
  [409, 'invoice already imported'],
])('shows the %s import error and retains the inputs', async (status, message) => {
  stubFetch({
    [`GET ${BASE}?${PAGE}`]: () => json([]),
    [`POST ${UPLOAD}`]: () => tmfError(status, message),
  });
  renderWholesale();
  fireEvent.click(screen.getByRole('button', { name: 'Import invoice' }));
  const dialog = within(screen.getByRole('dialog'));
  const number = dialog.getByLabelText<HTMLInputElement>(/Invoice number/);
  const fileInput = dialog.getByLabelText<HTMLInputElement>('Invoice CSV');
  const file = new File([CSV], 'invoice.csv', { type: 'text/csv' });
  fireEvent.change(number, { target: { value: invoice.invoiceNumber } });
  fireEvent.change(fileInput, { target: { files: [file] } });
  fireEvent.click(dialog.getByRole('button', { name: 'Import' }));

  expect((await dialog.findByRole('alert')).textContent).toContain(message);
  expect(number.value).toBe(invoice.invoiceNumber);
  expect(fileInput.files?.[0]).toBe(file);
});

it('opens invoice details, filters discrepancies and sorts paged margins on the server', async () => {
  const { calls } = stubFetch({
    [`GET ${BASE}?${PAGE}`]: () => json([invoice]),
    [`GET ${DETAIL}`]: () => json(invoice),
    [`GET ${DETAIL}/discrepancies?${PAGE}`]: () =>
      json([
        {
          id: 'd1',
          kind: 'usageMismatch',
          product: { id: PRODUCT },
          msisdn: '12025550199',
          usageKind: 'data',
          lineNumber: null,
          expected: 12.5,
          invoiced: 102.5,
          detail: {},
        },
      ]),
    [`GET ${DETAIL}/discrepancies?${PAGE}&kind=duplicateCharge`]: () => json([]),
    [`GET /tmf-api/productInventory/v5/product/${PRODUCT}`]: () =>
      json({ billingAccount: { id: ACCOUNT, name: 'Line account' } }),
    [`GET ${DETAIL}/lines?sort=margin&${PAGE}`]: () =>
      json(
        [
          {
            product: null,
            msisdn: '12025550199',
            iccid: null,
            invoiceLines: 1,
            cost,
            revenue,
            margin,
          },
        ],
        { more: true },
      ),
    [`GET ${DETAIL}/lines?sort=margin&offset=25&limit=25`]: () =>
      json([
        {
          product: null,
          msisdn: '12025550200',
          iccid: null,
          invoiceLines: 1,
          cost,
          revenue,
          margin,
        },
      ]),
    [`GET ${DETAIL}/lines?sort=-margin&${PAGE}`]: () =>
      json([
        {
          product: null,
          msisdn: '12025550201',
          iccid: null,
          invoiceLines: 1,
          cost,
          revenue,
          margin,
        },
      ]),
  });
  renderWholesale();
  const invoiceLink = await screen.findByRole('link', { name: invoice.invoiceNumber });
  expect(screen.getByText('2026-09-01 – 2026-09-30')).toBeTruthy();
  expect(screen.getByText('4')).toBeTruthy();
  fireEvent.click(invoiceLink);
  expectUi('ui.wholesale.detail');
  const link = await screen.findByRole('link', { name: 'Line account' });
  expect(link.getAttribute('href')).toBe(`/billing-accounts/${ACCOUNT}`);
  expect(screen.getByText('Line count')).toBeTruthy();
  const discrepancies = within(expectUi('ui.wholesale.discrepancies'));
  fireEvent.change(discrepancies.getByLabelText('Discrepancy kind'), {
    target: { value: 'duplicateCharge' },
  });
  await discrepancies.findByText('No matching discrepancies');

  fireEvent.click(screen.getByRole('tab', { name: 'Lines' }));
  const lines = within(expectUi('ui.wholesale.lines'));
  await lines.findByText('−1.20');
  expect(calls).toContain(`GET ${DETAIL}/lines?sort=margin&${PAGE}`);
  fireEvent.click(expectUi('ui.wholesale.page.next'));
  await lines.findByText('12025550200');
  expectUi('ui.wholesale.page.previous');
  fireEvent.change(lines.getByLabelText('Margin order'), { target: { value: '-margin' } });
  await lines.findByText('12025550201');
});

// #498: a cancelled import, Escape during an upload, Reset filters and a subscriber retry
it('cancels an import without sending it, and keeps the dialog on Escape while uploading', async () => {
  let answer: (response: Response) => void = () => {};
  const { fetchMock } = stubFetch({
    [`GET ${BASE}?${PAGE}`]: () => json([]),
    [`POST ${UPLOAD}`]: () =>
      new Promise<Response>((resolve) => {
        answer = resolve;
      }),
    [`GET ${DETAIL}`]: () => json(invoice),
    [`GET ${DETAIL}/discrepancies?${PAGE}`]: () => json([]),
  });
  renderWholesale();
  fireEvent.click(screen.getByRole('button', { name: 'Import invoice' }));
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);

  fireEvent.click(screen.getByRole('button', { name: 'Import invoice' }));
  const dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText(/Invoice number/), {
    target: { value: invoice.invoiceNumber },
  });
  fireEvent.change(within(dialog).getByLabelText('Invoice CSV'), {
    target: { files: [new File([CSV], 'invoice.csv')] },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Import' }));
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.getByRole('dialog')).toBe(dialog);
  answer(json(invoice, { status: 201 }));
  await screen.findByRole('heading', { name: invoice.invoiceNumber });
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('closes the import dialog on Escape when nothing is being sent', async () => {
  stubFetch({ [`GET ${BASE}?${PAGE}`]: () => json([]) });
  renderWholesale();
  fireEvent.click(screen.getByRole('button', { name: 'Import invoice' }));
  expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('resets the discrepancy kind from an empty filtered page and retries a subscriber', async () => {
  let productReads = 0;
  const row = {
    id: 'd1',
    kind: 'billedNotActive',
    product: { id: PRODUCT },
    msisdn: '12025550199',
    usageKind: null,
    lineNumber: 1,
    expected: null,
    invoiced: 1.2,
    detail: {},
  };
  stubFetch({
    [`GET ${DETAIL}`]: () => json(invoice),
    [`GET ${DETAIL}/discrepancies?${PAGE}`]: () => json([row]),
    [`GET ${DETAIL}/discrepancies?${PAGE}&kind=duplicateCharge`]: () => json([]),
    [`GET /tmf-api/productInventory/v5/product/${PRODUCT}`]: () =>
      ++productReads === 1
        ? tmfError(503, 'Unavailable')
        : json({ billingAccount: { id: ACCOUNT, name: 'Line account' } }),
  });
  renderWholesale(`/wholesale/${ID}`);
  const discrepancies = within(await waitForUi('ui.wholesale.discrepancies'));
  fireEvent.click(await discrepancies.findByRole('button', { name: 'Retry subscriber' }));
  expect(await discrepancies.findByRole('link', { name: 'Line account' })).toBeTruthy();
  fireEvent.change(discrepancies.getByLabelText('Discrepancy kind'), {
    target: { value: 'duplicateCharge' },
  });
  await discrepancies.findByText('No matching discrepancies');
  fireEvent.click(discrepancies.getByRole('button', { name: 'Reset filters' }));
  expect(await discrepancies.findByText('12025550199')).toBeTruthy();
  expect((discrepancies.getByLabelText('Discrepancy kind') as HTMLSelectElement).value).toBe('');
});

async function waitForUi(marker: string) {
  await screen.findByRole('heading', { name: invoice.invoiceNumber });
  return expectUi(marker);
}
