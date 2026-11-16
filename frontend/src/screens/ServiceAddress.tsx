import { useState } from 'react';
import { saveAccountContacts, type Account } from '../api/bss';
import { ApiError } from '../api/client';
import {
  addressErrors,
  contactsWith,
  US_STATES,
  type AddressField,
  type ServiceAddress,
} from '../api/serviceAddress';
import {
  Button,
  Dialog,
  Field,
  KeyValue,
  SelectField,
  submitDialogPrimary,
  TextInput,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { Footer, useWrite } from './FinancialForms';
import { AccountPinField } from './SecurityTab';

// The billing account's service address on the subscriber card (#207,
// docs/decisions/taxes.md): shown on the Account tab, set or changed in its own dialog,
// removed after a confirmation. Every write sends the account's whole `contact` array.

export const MISSING_TEXT =
  'No service address: new prepaid lines are refused and charges are taxed at the flat rate';

/** The card's warning for a prepaid account without a service address. */
export function MissingServiceAddress({ onEdit }: { onEdit: () => void }) {
  return (
    <div data-ui="ui.subscriber.serviceAddress.missing">
      <Note tone="warning">
        {MISSING_TEXT}.{' '}
        <button type="button" className="bss-link" onClick={onEdit}>
          Set service address…
        </button>
      </Note>
    </div>
  );
}

function addressLines(a: ServiceAddress): string[] {
  return [
    a.street1,
    ...(a.street2 ? [a.street2] : []),
    `${a.city}, ${a.stateOrProvince} ${a.postCode}`,
    'US',
  ];
}

/** The Service address group of the Account tab. */
export function ServiceAddressPanel({
  account,
  onEdit,
  onRemove,
}: {
  account: Account;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const address = account.serviceAddress ?? null;
  return (
    <section className="subscriber-account" data-ui="ui.subscriber.serviceAddress">
      <h2>Service address</h2>
      <KeyValue
        items={[
          [
            'Address',
            address === null ? (
              '—'
            ) : (
              <>
                {addressLines(address).map((line) => (
                  <div key={line}>{line}</div>
                ))}
              </>
            ),
          ],
          ['Used for', 'Taxes on prepaid charges (place of primary use)'],
        ]}
      />
      <div className="bss-bill-actions">
        <Button small onClick={onEdit}>
          {address === null ? 'Set service address' : 'Edit service address'}
        </Button>
        {address !== null && (
          <Button small tone="negative" onClick={onRemove}>
            Remove
          </Button>
        )}
      </div>
    </section>
  );
}

const EMPTY: ServiceAddress = {
  street1: '',
  street2: '',
  city: '',
  stateOrProvince: '',
  postCode: '',
};

/** Set or change the service address. The server validates it (a 422 shows at the input)
 * and alone knows whether the customer is protected, so the account PIN is always offered. */
export function ServiceAddressDialog({
  account,
  onClose,
  onDone,
}: {
  account: Account;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const before = account.serviceAddress ?? null;
  const [form, setForm] = useState<ServiceAddress>(before ?? EMPTY);
  const [pin, setPin] = useState('');
  const [errors, setErrors] = useState<Partial<Record<AddressField, string>>>({});
  const write = useWrite(onDone);
  const set = (field: AddressField, value: string) => {
    setForm({ ...form, [field]: value });
    setErrors({ ...errors, [field]: undefined });
  };
  const ready =
    form.street1.trim() !== '' &&
    form.city.trim() !== '' &&
    form.stateOrProvince !== '' &&
    form.postCode.trim() !== '';
  const save = () =>
    void write.run(async () => {
      setErrors({});
      try {
        await saveAccountContacts(account.id, contactsWith(account.contact ?? [], form), pin);
      } catch (error) {
        const { fields, rest } = addressErrors(error);
        if (!(error instanceof ApiError) || Object.keys(fields).length === 0) throw error;
        setErrors(fields);
        // a refusal (never "unsure"): the footer says what no input shows
        throw new ApiError(
          error.method,
          error.path,
          error.status,
          error.reason,
          rest.length ? rest.join('; ') : 'Correct the marked fields.',
        );
      }
      setPin('');
      return before === null ? 'Service address set' : 'Service address changed';
    });
  const input = (field: AddressField, label: string, extra: { help?: string } = {}) => (
    <Field label={label} error={errors[field]} {...extra}>
      <TextInput
        aria-label={label}
        aria-invalid={errors[field] ? true : undefined}
        value={form[field]}
        onChange={(e) => set(field, e.target.value)}
      />
    </Field>
  );
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={before === null ? 'Set service address' : 'Edit service address'}
      size="M"
      onClose={onClose}
      ui="ui.subscriber.serviceAddress.edit"
      context={account.customerName}
      footer={
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            data-dialog-primary
            busyLabel="Saving…"
            disabled={!ready}
            onClick={save}
          >
            Save
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <fieldset
          className="bss-form bss-write-fields"
          disabled={write.busy}
          data-ui="ui.subscriber.serviceAddress.form"
        >
          <p className="caption">
            The line’s place of primary use: prepaid charges are taxed for it. US addresses only.
          </p>
          {input('street1', 'Street')}
          {input('street2', 'Street, line 2', { help: 'Optional' })}
          {input('city', 'City')}
          <Field label="State" error={errors.stateOrProvince}>
            <SelectField
              aria-label="State"
              aria-invalid={errors.stateOrProvince ? true : undefined}
              value={form.stateOrProvince}
              onChange={(e) => set('stateOrProvince', e.target.value)}
            >
              <option value="">Choose a state</option>
              {US_STATES.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </SelectField>
          </Field>
          {input('postCode', 'ZIP code', { help: '12345 or 12345-6789' })}
          <Field label="Country">
            <TextInput aria-label="Country" value="US" readOnly />
          </Field>
          <AccountPinField value={pin} onChange={setPin} />
        </fieldset>
      </div>
    </Dialog>
  );
}

/** Remove the service address: the contact array without its serviceAddress entry. */
export function RemoveServiceAddressDialog({
  account,
  onClose,
  onDone,
}: {
  account: Account;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [pin, setPin] = useState('');
  const write = useWrite(onDone);
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Remove service address"
      size="M"
      onClose={onClose}
      ui="ui.subscriber.serviceAddress.remove"
      context={account.customerName}
      footer={
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            data-dialog-primary
            onClick={() =>
              void write.run(async () => {
                await saveAccountContacts(
                  account.id,
                  contactsWith(account.contact ?? [], null),
                  pin,
                );
                setPin('');
                return 'Service address removed';
              })
            }
          >
            Remove
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <fieldset className="bss-form bss-write-fields" disabled={write.busy}>
          <p>
            {account.ratingType === 'prepaid'
              ? 'Without a service address new prepaid lines are refused and charges are taxed at the flat rate. Charges already booked keep their taxes.'
              : 'Charges already booked keep their taxes.'}
          </p>
          <AccountPinField value={pin} onChange={setPin} />
        </fieldset>
      </div>
    </Dialog>
  );
}
