import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { unlabeledControls } from '../test/unlabeled';
import {
  Button,
  Checkbox,
  DateField,
  Field,
  FileField,
  MoneyField,
  Segmented,
  SelectField,
} from './kit';
import { expectUi } from '../test/expectUi';
import { UiKitScreen } from '../screens/UiKit';

describe('Ledger controls', () => {
  it('disables busy buttons and keeps their name unless a busyLabel is given', () => {
    const { rerender } = render(<Button busy>Save</Button>);
    const button = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.classList.contains('is-busy')).toBe(true);
    expect(button.getAttribute('aria-busy')).toBe('true');
    rerender(
      <Button busy busyLabel="Saving…">
        Save
      </Button>,
    );
    expect(screen.getByRole('button', { name: 'Saving…' })).toBe(button);
  });

  it('offers radio semantics and reports the selected key', () => {
    const change = vi.fn();
    render(
      <Segmented
        as="radio"
        label="Account mode"
        value="prepaid"
        onChange={change}
        options={[
          { key: 'prepaid', label: 'Prepaid' },
          { key: 'postpaid', label: 'Postpaid' },
        ]}
      />,
    );
    expect(screen.getByRole('radiogroup', { name: 'Account mode' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Prepaid' }).getAttribute('aria-checked')).toBe(
      'true',
    );
    const postpaid = screen.getByRole('radio', { name: 'Postpaid' });
    expect(postpaid.hasAttribute('aria-pressed')).toBe(false);
    fireEvent.click(postpaid);
    expect(change).toHaveBeenCalledWith('postpaid');
  });

  it('keeps a typed money string and renders its separate currency suffix', () => {
    function Form() {
      const [value, setValue] = useState('');
      return (
        <Field label="Amount" help="Amount received" error="Check the amount">
          <MoneyField
            currency="EUR"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </Field>
      );
    }
    render(<Form />);
    const input = screen.getByRole('textbox') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '001.20' } });
    expect(input.value).toBe('001.20');
    expect(screen.getByText('EUR').className).toBe('money-suffix');
    expect(screen.getByText('Check the amount').className).toBe('field-error');
    expect(screen.getByText('Amount received').className).toBe('field-help');
  });

  it('preserves select, ISO date and checkbox values within their labels', () => {
    render(
      <>
        <Field label="Mode">
          <SelectField>
            <option>Prepaid</option>
            <option>Postpaid</option>
          </SelectField>
        </Field>
        <Field label="Start date">
          <DateField defaultValue="2026-10-26" />
        </Field>
        <Field label="Confirmed">
          <Checkbox />
        </Field>
      </>,
    );
    fireEvent.change(screen.getByLabelText('Mode'), { target: { value: 'Postpaid' } });
    expect((screen.getByLabelText('Mode') as HTMLSelectElement).value).toBe('Postpaid');
    expect((screen.getByLabelText('Start date') as HTMLInputElement).value).toBe('2026-10-26');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Confirmed' }));
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
  });

  it('renders the request-free gallery and its ui marker', () => {
    render(<UiKitScreen />);
    expect(
      screen.getByRole('heading', { name: 'UI kit' }).parentElement?.getAttribute('data-ui'),
    ).toBe('ui.kit');
    expectUi('ui.kit');
    expect(
      within(expectUi('ui.uikit.combobox')).getByRole('combobox', { name: 'Empty combobox' }),
    ).toBeTruthy();
    expect(screen.getByRole('radiogroup', { name: 'Choose mode' })).toBeTruthy();
    const unlabeled = Array.from(
      document.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(
        'input, select, textarea',
      ),
    ).filter(
      (control) =>
        control.labels?.length === 0 && (control.getAttribute('aria-labelledby') ?? '') === '',
    );
    expect(unlabeled).toEqual([]);
  });

  it('renders a checkbox with text as one clickable label (B12)', () => {
    const { container } = render(<Checkbox label="Sellable" />);
    const label = container.querySelector('label.bss-check');
    expect(label?.textContent).toBe('Sellable');
    fireEvent.click(screen.getByLabelText('Sellable'));
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
  });

  it('shows the chosen file name in a FileField (B26)', () => {
    const onFile = vi.fn();
    render(<FileField label="JSON file" fileName="catalog.json" onFile={onFile} />);
    expect(screen.getByText('catalog.json')).toBeTruthy();
    const file = new File(['{}'], 'catalog.json');
    fireEvent.change(screen.getByLabelText('JSON file'), { target: { files: [file] } });
    expect(onFile).toHaveBeenCalledWith(file);
  });

  it('reports a control without a label or aria name (B27)', () => {
    const { container } = render(
      <>
        <input id="a" />
        <label htmlFor="b">B</label>
        <input id="b" />
        <input aria-label="C" />
      </>,
    );
    expect(unlabeledControls(container)).toEqual(['<input id="a">']);
    cleanup();
  });
});
