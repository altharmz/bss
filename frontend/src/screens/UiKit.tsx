import { useState } from 'react';
import {
  Button,
  Checkbox,
  DateField,
  Field,
  MoneyField,
  Segmented,
  SelectField,
  TextInput,
} from '../ui/kit';
import './screens.css';

/** Ledger Components artboard: live controls without screen-specific requests. */
export function UiKitScreen() {
  const [mode, setMode] = useState('prepaid');
  const [money, setMoney] = useState('42.10');
  return (
    <div className="ui-kit" data-ui="ui.kit">
      <h1>UI kit</h1>
      <section>
        <h2>Buttons</h2>
        {(['primary', 'secondary', 'danger', 'quiet'] as const).map((variant) => (
          <div className="ui-kit-row" key={variant}>
            <Button variant={variant}>{variant}</Button>
            <Button variant={variant} small>
              Small
            </Button>
            <Button variant={variant} disabled>
              Disabled
            </Button>
            <Button variant={variant} busy>
              Saving
            </Button>
            <Button variant={variant} busy busyLabel="Saving…">
              Save
            </Button>
          </div>
        ))}
        <Button variant="quiet" tone="negative">
          Delete…
        </Button>
      </section>
      <section>
        <h2>Fields</h2>
        <div className="ui-kit-fields">
          <Field label="Name" help="As shown on the account">
            <TextInput placeholder="Subscriber name" />
          </Field>
          <Field label="Account" error="Account is required">
            <TextInput aria-invalid="true" />
          </Field>
          <Field label="Disabled">
            <TextInput value="Unavailable" disabled readOnly />
          </Field>
          <Field label="Mode">
            <SelectField defaultValue="prepaid">
              <option value="prepaid">Prepaid</option>
              <option value="postpaid">Postpaid</option>
            </SelectField>
          </Field>
          <Field label="Start date">
            <DateField defaultValue="2026-10-26" />
          </Field>
          <Field label="Amount">
            <MoneyField currency="EUR" value={money} onChange={(e) => setMoney(e.target.value)} />
          </Field>
          <Field label="Confirmed">
            <Checkbox />
          </Field>
        </div>
      </section>
      <section>
        <h2>Segmented</h2>
        <div className="ui-kit-row">
          <Segmented
            label="Account mode"
            value={mode}
            onChange={setMode}
            options={[
              { key: 'prepaid', label: 'Prepaid', count: 14 },
              { key: 'postpaid', label: 'Postpaid', count: 8 },
            ]}
          />
          <Segmented
            as="radio"
            label="Choose mode"
            value={mode}
            onChange={setMode}
            options={[
              { key: 'prepaid', label: 'Prepaid' },
              { key: 'postpaid', label: 'Postpaid' },
            ]}
          />
        </div>
      </section>
    </div>
  );
}
