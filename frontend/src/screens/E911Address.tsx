import { useRef, useState } from 'react';
import type { AccountProduct } from '../api/bss';
import { ApiError, explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import {
  changeE911,
  e911Errors,
  e911Ready,
  e911Text,
  formOf,
  EMPTY_E911,
  type E911Field,
  type E911Form,
} from '../api/e911';
import { orderRefusal } from '../api/orderErrors';
import { US_STATES } from '../api/serviceAddress';
import { Button, Dialog, Field, SelectField, TextInput, submitDialogPrimary } from '../ui/kit';
import { Footer } from './FinancialForms';
import { AccountPinField } from './SecurityTab';

// The E911 address of a Wi-Fi calling line (#384, docs/decisions/network.md#e911-address-issue-201):
// the fields Add product and Edit E911 address share, the dialog that places the change order
// and the product row's line. The server validates the address and decides who needs the PIN.

/** The address inputs: street number, street, suffix, line 2, city, state, ZIP; US only. */
export function E911Fields({
  form,
  onChange,
  errors = {},
  readOnly = false,
}: {
  form: E911Form;
  onChange: (form: E911Form) => void;
  errors?: Partial<Record<E911Field, string>>;
  readOnly?: boolean;
}) {
  const input = (field: E911Field, label: string, help?: string) => (
    <Field label={label} error={errors[field]} {...(help ? { help } : {})}>
      <TextInput
        aria-label={label}
        aria-invalid={errors[field] ? true : undefined}
        readOnly={readOnly}
        value={form[field]}
        onChange={(e) => onChange({ ...form, [field]: e.target.value })}
      />
    </Field>
  );
  return (
    <>
      {input('streetNr', 'Street number', 'Optional')}
      {input('streetName', 'Street')}
      {input('streetSuffix', 'Street suffix', 'Optional: St, Ave, Terrace')}
      {input('line2', 'Apartment, suite or floor', 'Optional')}
      {input('city', 'City')}
      <Field label="State" error={errors.stateOrProvince}>
        <SelectField
          aria-label="State"
          aria-invalid={errors.stateOrProvince ? true : undefined}
          disabled={readOnly}
          value={form.stateOrProvince}
          onChange={(e) => onChange({ ...form, stateOrProvince: e.target.value })}
        >
          <option value="">Choose a state</option>
          {US_STATES.map((code) => (
            <option key={code} value={code}>
              {code}
            </option>
          ))}
        </SelectField>
      </Field>
      {input('postcode', 'ZIP code', '12345 or 12345-6789')}
    </>
  );
}

/** Whether a row is a line that holds an E911 address: a prepaid product that is not an
 * add-on pack (a pack's plan line keeps the address), with Wi-Fi calling or an address. */
function holdsE911(product: AccountProduct, prepaid: boolean): boolean {
  return (
    prepaid &&
    product.offeringKind !== 'addOn' &&
    (product.wifiCalling === true || (product.e911Address ?? null) !== null)
  );
}

/** The product row's E911 line: the registered address, or that a Wi-Fi calling line has
 * none. */
export function E911Line({ product, prepaid }: { product: AccountProduct; prepaid: boolean }) {
  if (!holdsE911(product, prepaid)) return null;
  const address = product.e911Address ?? null;
  return (
    <div className="bss-secondary" data-ui="ui.subscriber.e911">
      E911: {address === null ? 'no address registered' : e911Text(address)}
    </div>
  );
}

/** Whether the row offers Edit E911 address: a started line that holds an address, without
 * a change already waiting for the network. */
export function offersE911Edit(product: AccountProduct, prepaid: boolean): boolean {
  return (
    holdsE911(product, prepaid) &&
    (product.status === 'active' || product.status === 'suspended') &&
    !product.pendingChanges.some((c) => c.kind === 'e911')
  );
}

/** Edit E911 address: a modify order whose only content is the new address. On a Wi-Fi
 * calling line it waits for the network, and the old address stays until it confirms. */
export function E911Dialog({
  accountId,
  product,
  onClose,
  onDone,
  onRefresh,
}: {
  accountId: string;
  product: AccountProduct;
  onClose: () => void;
  onDone: (message: string, accepted?: PendingOrder) => void;
  onRefresh: () => void;
}) {
  const before = product.e911Address ?? null;
  const [form, setForm] = useState<E911Form>(before === null ? EMPTY_E911 : formOf(before));
  const [pin, setPin] = useState('');
  const [errors, setErrors] = useState<Partial<Record<E911Field, string>>>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [code, setCode] = useState<string | undefined>();
  const [unsure, setUnsure] = useState(false);
  const saving = useRef(false);
  const name = product.productOffering.name;
  const ready = e911Ready(form);

  async function submit() {
    if (!ready || saving.current) return;
    saving.current = true;
    setBusy(true);
    setProblem(null);
    setCode(undefined);
    setErrors({});
    try {
      const result = await changeE911(accountId, product.id, form, pin);
      if (result.state === 'completed') {
        onClose();
        onDone(`${name}: E911 address changed`);
      } else if (result.state === 'inProgress') {
        onClose();
        onDone(`${name}: E911 address change accepted, it takes effect when the network confirms`, {
          id: result.id,
          label: `E911 address change of ${name}`,
        });
      } else {
        const refusal = orderRefusal(result.productOrderErrorMessage ?? [], result.state);
        setProblem(refusal.problem);
        setCode(refusal.code);
      }
    } catch (error) {
      const { fields, rest } = e911Errors(error);
      if (Object.keys(fields).length > 0) {
        setErrors(fields);
        setProblem(rest.length ? rest.join('; ') : 'Correct the marked fields.');
      } else if (error instanceof ApiError && error.status > 0 && error.status < 500) {
        setProblem(explain(error));
      } else {
        setUnsure(true);
        setProblem(`${explain(error)}. Unsure whether it was saved: Refresh to check.`);
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={`Edit E911 address: ${name}`}
      size="M"
      onClose={() => {
        if (!saving.current) onClose();
      }}
      ui="ui.subscriber.e911.form"
      footer={
        <Footer
          write={{ busy, problem, unsure, code }}
          onRefresh={() => {
            onClose();
            onRefresh();
          }}
        >
          <Button disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            data-dialog-primary
            disabled={!ready || busy || unsure}
            onClick={() => void submit()}
          >
            Save E911 address
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <fieldset className="bss-form bss-write-fields" disabled={busy}>
          <p className="caption">
            {product.wifiCalling
              ? 'Where emergency services are sent for a 911 call made over Wi-Fi. The network registers the new address; until it confirms, the line keeps the current one.'
              : 'Kept for the line and sent to the network once its offering enables Wi-Fi calling.'}{' '}
            US addresses only.
          </p>
          <E911Fields form={form} errors={errors} onChange={setForm} />
          <AccountPinField value={pin} onChange={setPin} />
        </fieldset>
      </div>
    </Dialog>
  );
}
