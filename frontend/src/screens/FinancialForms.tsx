import { useState } from 'react';
import type { AccountState, FinancialAccount } from '../api/bss';
import { explain } from '../api/client';
import {
  createBody,
  createFinancial,
  deleteFinancial,
  formOf,
  loadOwnerChoices,
  patchBody,
  patchFinancial,
  type FinancialForm,
  type OwnerChoice,
} from '../api/financial';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { parseAmount } from '../format/money';
import { Button, Modal, Segmented, TextInput } from '../ui/kit';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { Field } from './SubscriberForms';
import { uncertain } from './submissionKey';

// New, edit and delete of a financial account (docs/next-slices-design.md section 16).
// The server decides: its 409/422 text is shown in the dialog and the entered values stay.
// After an uncertain result (no answer, 5xx) the dialog offers Refresh, never a retry.

const STATES: AccountState[] = ['Active', 'Suspended', 'Closed'];

/** Save/Delete outcome state shared by both dialogs. */
function useWrite(onDone: (message: string) => void) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);
  async function run(action: () => Promise<string>) {
    setBusy(true);
    setProblem(null);
    setUnsure(false);
    try {
      onDone(await action());
    } catch (error) {
      if (uncertain(error)) {
        setUnsure(true);
        setProblem(`${explain(error)}. Unsure whether it was saved: Refresh to check.`);
      } else {
        setProblem(explain(error));
      }
    } finally {
      setBusy(false);
    }
  }
  return { busy, problem, unsure, run };
}

function Footer({
  write,
  onRefresh,
  children,
}: {
  write: ReturnType<typeof useWrite>;
  onRefresh: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      {write.problem !== null && (
        <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
          {write.problem}
        </div>
      )}
      {write.unsure && <Button onClick={onRefresh}>Refresh</Button>}
      {children}
    </>
  );
}

export function FinancialFormModal({
  account,
  onClose,
  onDone,
}: {
  /** the account to edit; none = a new one */
  account: FinancialAccount | null;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const owners = useResource('financial-owners', loadOwnerChoices);
  const title = account ? `Edit ${account.name}` : 'New financial account';
  return (
    <Modal title={title} width={560} onClose={onClose} ui="ui.financialAccounts.form">
      {owners.status === 'loading' && (
        <div className="bss-modal-body">
          <SkeletonRows columns={[{ key: 'a', label: '', width: '1fr' }]} rows={5} />
        </div>
      )}
      {owners.status === 'error' && (
        <div className="bss-modal-body">
          <ErrorBlock
            title="Failed to load the parties"
            error={owners.error}
            onRetry={owners.retry}
          />
        </div>
      )}
      {owners.status === 'ok' && (
        <Body account={account} owners={owners.data} onClose={onClose} onDone={onDone} />
      )}
    </Modal>
  );
}

function Body({
  account,
  owners,
  onClose,
  onDone,
}: {
  account: FinancialAccount | null;
  owners: OwnerChoice[];
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const context = useSystemContext();
  const before: FinancialForm = account
    ? formOf(account, owners.find((o) => o.id === account.ownerId) ?? null, context.currency)
    : {
        name: '',
        owner: null,
        accountType: '',
        description: '',
        creditLimit: null,
        currency: context.currency,
        state: 'Active',
      };
  const [form, setForm] = useState(before);
  const [limitText, setLimitText] = useState(before.creditLimit ?? '');
  const write = useWrite(onDone);
  const set = (change: Partial<FinancialForm>) => setForm({ ...form, ...change });

  const limit = limitText.trim() === '' ? { ok: true, value: null } : parse(limitText);
  const after: FinancialForm = { ...form, creditLimit: limit.value };
  const currencyOk = limit.value === null || /^[A-Z]{3}$/.test(form.currency);
  const valid = form.name.trim() !== '' && form.owner !== null && limit.ok && currencyOk;
  const body = account ? patchBody(account, before, after) : createBody(after);

  return (
    <>
      <div className="bss-modal-body">
        <section className="bss-form">
          <Field label="Name">
            <TextInput value={form.name} onChange={(e) => set({ name: e.target.value })} />
          </Field>
          <Field label="Owner">
            <select
              className="bss-input"
              value={form.owner?.id ?? ''}
              onChange={(e) => set({ owner: owners.find((o) => o.id === e.target.value) ?? null })}
            >
              {form.owner === null && <option value="">Choose a party</option>}
              {owners.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name} · {o.kind === 'Individual' ? 'person' : 'organization'}
                </option>
              ))}
            </select>
          </Field>
          {account && (
            <div className="caption">
              The owner cannot change while billing, party or settlement accounts link to this
              financial account; the server refuses it.
            </div>
          )}
          <Field label="Account type">
            <TextInput
              value={form.accountType}
              onChange={(e) => set({ accountType: e.target.value })}
            />
          </Field>
          <Field label="Description">
            <TextInput
              value={form.description}
              onChange={(e) => set({ description: e.target.value })}
            />
          </Field>
          <Field label="Credit limit (informational, empty = none)">
            <TextInput
              mono
              width={160}
              value={limitText}
              onChange={(e) => setLimitText(e.target.value)}
            />
          </Field>
          <Field label="Credit limit currency">
            <TextInput
              mono
              width={80}
              value={form.currency}
              onChange={(e) => set({ currency: e.target.value.toUpperCase() })}
            />
          </Field>
          {!limit.ok && (
            <div className="bss-inline-error">Enter a credit limit with at most 2 decimals.</div>
          )}
          {!currencyOk && <div className="bss-inline-error">The currency is 3 letters.</div>}
          <Segmented
            label="State"
            value={form.state}
            onChange={(state) => set({ state })}
            options={STATES.map((key) => ({ key, label: key }))}
          />
        </section>
      </div>
      <footer className="bss-modal-footer">
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!valid || body === null || write.busy}
            onClick={() =>
              body !== null &&
              void write.run(async () => {
                if (account) {
                  await patchFinancial(account.id, body);
                  return `${form.name.trim()} saved`;
                }
                await createFinancial(body);
                return `${form.name.trim()} created`;
              })
            }
          >
            {account ? 'Save' : 'Create'}
          </Button>
        </Footer>
      </footer>
    </>
  );
}

function parse(text: string): { ok: boolean; value: string | null } {
  const value = parseAmount(text);
  return { ok: value !== null, value };
}

export function DeleteFinancialModal({
  account,
  onClose,
  onDone,
}: {
  account: FinancialAccount;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const write = useWrite(onDone);
  return (
    <Modal
      title={`Delete ${account.name}`}
      width={480}
      onClose={onClose}
      ui="ui.financialAccounts.confirmDelete"
      footer={
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            disabled={write.busy}
            onClick={() =>
              void write.run(async () => {
                await deleteFinancial(account.id);
                return `${account.name} deleted`;
              })
            }
          >
            Delete
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <p>Deletes the financial account. No account links to it.</p>
      </div>
    </Modal>
  );
}
