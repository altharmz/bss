// #498: the catalog's dialogs around a write - cancelled and dismissed dialogs send nothing,
// a nested dialog closes alone, and a refused price keeps what the operator typed.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, tmfError, type Handler } from '../test/fetch';
import { CAT, ids, OFFERINGS, PRICES, backend, open, row } from '../test/catalogKit';

type FetchMock = ReturnType<typeof backend>['fetchMock'];
const writes = (fetchMock: FetchMock) =>
  fetchMock.mock.calls
    .filter(([, init]) => init?.method !== undefined && init.method !== 'GET')
    .map(([url, init]) => `${init?.method} ${String(url)}`);
const escape = () => fireEvent.keyDown(window, { key: 'Escape' });

async function details(name: string) {
  await open();
  await waitFor(() => row(name));
  fireEvent.click(row(name));
  return screen.getByRole('dialog', { name });
}

describe('catalog dialogs that send nothing', () => {
  it('cancels a new specification from the Specifications tab', async () => {
    const { fetchMock } = backend();
    await open();
    fireEvent.click(screen.getByRole('tab', { name: 'Specifications' }));
    const add = await screen.findByRole('button', { name: 'New specification' });
    await waitFor(() => expect((add as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(add);
    const dialog = screen.getByRole('dialog', { name: 'New product specification' });
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'Roaming' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
  });

  it('cancels a new offering', async () => {
    const { fetchMock } = backend();
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(screen.getByRole('button', { name: 'New offering' }));
    const dialog = screen.getByRole('dialog', { name: 'New product offering' });
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: 'Mobile XL' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(writes(fetchMock)).toEqual([]);
  });

  it('closes only the specification dialog opened from New offering on Escape', async () => {
    const { fetchMock } = backend();
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(screen.getByRole('button', { name: 'New offering' }));
    const offering = screen.getByRole('dialog', { name: 'New product offering' });
    fireEvent.change(within(offering).getByLabelText(/^Name/), {
      target: { value: 'Mobile XL' },
    });
    fireEvent.click(within(offering).getByRole('button', { name: 'New specification…' }));
    expect(screen.getByRole('dialog', { name: 'New product specification' })).toBeTruthy();
    escape();
    expect(screen.queryByRole('dialog', { name: 'New product specification' })).toBeNull();
    // the offering form stays open with what was typed
    expect(screen.getByRole('dialog', { name: 'New product offering' })).toBe(offering);
    expect((within(offering).getByLabelText(/^Name/) as HTMLInputElement).value).toBe('Mobile XL');
    expect(writes(fetchMock)).toEqual([]);
  });

  it('answers No to deleting an offering and sends no DELETE', async () => {
    const tv = { ...OFFERINGS[2], productOfferingPrice: [] };
    const list = OFFERINGS.map((o) => (o.id === ids(3) ? tv : o));
    const { fetchMock } = backend({}, list);
    const dialog = await details('TV Basic');
    // Delete is offered once the detail shows the offering has no price
    await waitFor(() => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Offering actions' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }));
    });
    expect(within(dialog).getByText('Delete this offering?')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'No' }));
    expect(within(dialog).queryByText('Delete this offering?')).toBeNull();
    expect(screen.getByRole('dialog', { name: 'TV Basic' })).toBe(dialog);
    expect(writes(fetchMock)).toEqual([]);
  });

  it('cancels an offering edit, and Escape in the edit leaves the detail open', async () => {
    const { fetchMock } = backend();
    const dialog = await details('Mobile L');
    const editOffering = async () => {
      fireEvent.click(await within(dialog).findByRole('button', { name: 'Offering actions' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Edit offering' }));
      return screen.getByRole('dialog', { name: 'Edit Mobile L' });
    };
    const edit = await editOffering();
    fireEvent.change(within(edit).getByLabelText(/^Name/), { target: { value: 'Mobile XL' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Edit Mobile L' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Mobile L' })).toBe(dialog);
    await editOffering();
    escape();
    expect(screen.queryByRole('dialog', { name: 'Edit Mobile L' })).toBeNull();
    // the detail ignores the Escape meant for the dialog above it
    expect(screen.getByRole('dialog', { name: 'Mobile L' })).toBe(dialog);
    expect(writes(fetchMock)).toEqual([]);
  });

  it('cancels a price opened from the detail', async () => {
    const { fetchMock } = backend({
      [`GET ${CAT}/productOfferingPrice/pr1`]: () =>
        json({ ...PRICES[0], name: 'Mobile L monthly', priceType: 'recurring' }),
    });
    const dialog = await details('Mobile L');
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Open price 1,290.00' }));
    const price = await screen.findByRole('dialog', { name: 'Mobile L monthly' });
    fireEvent.click(within(price).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Mobile L monthly' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Mobile L' })).toBe(dialog);
    expect(writes(fetchMock)).toEqual([]);
  });

  it('cancels a price change and sends nothing', async () => {
    const { fetchMock } = backend();
    const dialog = await details('Mobile L');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Change price…' }));
    const form = within(dialog).getByRole('region', { name: 'Change price' });
    fireEvent.change(within(form).getByLabelText(/New price, USD/), { target: { value: '1390' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }));
    expect(within(dialog).queryByRole('region', { name: 'Change price' })).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Change price…' })).toBeTruthy();
    expect(writes(fetchMock)).toEqual([]);
  });
});

describe('catalog writes with a date or a refusal', () => {
  it('creates an offering from the chosen Valid from date', async () => {
    const { fetchMock } = backend({
      [`POST ${CAT}/productOfferingPrice`]: () => json({ id: 'new-price' }, { status: 201 }),
      [`POST ${CAT}/productOffering`]: () => json({ id: ids(9) }, { status: 201 }),
    });
    await open();
    await waitFor(() => row('Mobile L'));
    fireEvent.click(screen.getByRole('button', { name: 'New offering' }));
    const dialog = screen.getByRole('dialog', { name: 'New product offering' });
    const field = (label: RegExp) => within(dialog).getByLabelText(label);
    await waitFor(() => expect((field(/Categories/) as HTMLSelectElement).disabled).toBe(false));
    fireEvent.change(field(/Code/), { target: { value: 'mob-xl' } });
    fireEvent.change(field(/^Name/), { target: { value: 'Mobile XL' } });
    fireEvent.change(field(/Price, USD/), { target: { value: '10' } });
    fireEvent.change(field(/Valid from/), { target: { value: '2026-11-01' } });
    expect((field(/Valid to/) as HTMLInputElement).min).toBe('2026-11-01');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create as In design' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Offering MOB-XL created in In design',
    );
    const [priceCall, offeringCall] = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === 'POST',
    );
    const validFor = { startDateTime: '2026-11-01T00:00:00+03:00' };
    expect(JSON.parse(String(priceCall?.[1]?.body)).validFor).toEqual(validFor);
    expect(JSON.parse(String(offeringCall?.[1]?.body)).validFor).toEqual(validFor);
  });

  it('keeps a refused price with the typed amount and date, then adds it once corrected', async () => {
    let refuse = true;
    const routes: Record<string, Handler> = {
      [`POST ${CAT}/productOfferingPrice`]: () =>
        refuse
          ? tmfError(409, 'a one-time price in USD already starts on 2026-10-15')
          : json({ id: 'new-price' }, { status: 201 }),
      [`PATCH ${CAT}/productOffering/${ids(1)}`]: () => json({ id: ids(1) }),
    };
    const { fetchMock } = backend(routes);
    const detail = await details('Mobile L');
    fireEvent.click(await within(detail).findByRole('button', { name: 'Add price…' }));
    const dialog = screen.getByRole('dialog', { name: 'Add price · Mobile L' });
    fireEvent.change(within(dialog).getByLabelText('Amount, USD'), { target: { value: '25' } });
    fireEvent.change(within(dialog).getByLabelText('Valid from'), {
      target: { value: '2026-10-15' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add price' }));
    expect(
      await within(dialog).findByText(/a one-time price in USD already starts on 2026-10-15/),
    ).toBeTruthy();
    expect((within(dialog).getByLabelText('Amount, USD') as HTMLInputElement).value).toBe('25');
    expect((within(dialog).getByLabelText('Valid from') as HTMLInputElement).value).toBe(
      '2026-10-15',
    );
    // nothing was attached to the offering
    expect(writes(fetchMock)).toEqual([`POST ${CAT}/productOfferingPrice`]);
    refuse = false;
    fireEvent.change(within(dialog).getByLabelText('Valid from'), {
      target: { value: '2026-10-16' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add price' }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Price added to Mobile L',
    );
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(posts[1]?.[1]?.body)).validFor).toEqual({
      startDateTime: '2026-10-16T00:00:00+03:00',
    });
  });

  it('keeps the add price dialog open on Escape while the price is being sent', async () => {
    let answer: (response: Response) => void = () => {};
    backend({
      [`POST ${CAT}/productOfferingPrice`]: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
      [`PATCH ${CAT}/productOffering/${ids(1)}`]: () => json({ id: ids(1) }),
    });
    const detail = await details('Mobile L');
    fireEvent.click(await within(detail).findByRole('button', { name: 'Add price…' }));
    const dialog = screen.getByRole('dialog', { name: 'Add price · Mobile L' });
    fireEvent.change(within(dialog).getByLabelText('Amount, USD'), { target: { value: '25' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add price' }));
    await waitFor(() =>
      expect(
        (within(dialog).getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled,
      ).toBe(true),
    );
    escape();
    expect(screen.getByRole('dialog', { name: 'Add price · Mobile L' })).toBe(dialog);
    expect(screen.getByRole('dialog', { name: 'Mobile L' })).toBe(detail);
    answer(json({ id: 'new-price' }, { status: 201 }));
    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Price added to Mobile L',
    );
  });
});
