import { useState } from 'react';
import type { AccountRecord, AccountState } from '../api/bss';
import { explain } from '../api/client';
import {
  createAccount,
  createBody,
  deleteAccount,
  formOf,
  loadFinancialChoices,
  loadOwnerChoices,
  patchAccount,
  patchBody,
  type AccountKind,
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

// The party and settlement tabs reuse these dialogs (docs/next-slices-design.md section 17).
const NAME: Record<AccountKind, string> = {
  FinancialAccount: 'financial account',
  PartyAccount: 'party account',
  SettlementAccount: 'settlement account',
};
const MARKER: Record<AccountKind, string> = {
  FinancialAccount: 'ui.financialAccounts',
  PartyAccount: 'ui.accounts.party',
  SettlementAccount: 'ui.accounts.settlement',
};
type Editable = AccountRecord & { financial?: { id: string; name: string } | null };

/** Save/Delete outcome state shared by both dialogs. */
export function useWrite(onDone: (message: string) => void) {
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

export function Footer({
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
  kind = 'FinancialAccount',
  onClose,
  onDone,
}: {
  /** the account to edit; none = a new one */
  account: Editable | null;
  kind?: AccountKind;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const owners = useResource('financial-owners', loadOwnerChoices);
  const title = account ? `Edit ${account.name}` : `New ${NAME[kind]}`;
  return (
    <Modal title={title} width={560} onClose={onClose} ui={`${MARKER[kind]}.form`}>
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
        <Body
          account={account}
          kind={kind}
          owners={owners.data}
          onClose={onClose}
          onDone={onDone}
        />
      )}
    </Modal>
  );
}

function Body({
  account,
  kind,
  owners,
  onClose,
  onDone,
}: {
  account: Editable | null;
  kind: AccountKind;
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
        financialId: null,
      };
  const [form, setForm] = useState(before);
  const [limitText, setLimitText] = useState(before.creditLimit ?? '');
  const write = useWrite(onDone);
  const set = (change: Partial<FinancialForm>) => setForm({ ...form, ...change });
  const linkable = kind !== 'FinancialAccount';
  const ownerId = form.owner?.id ?? '';
  // the owner's financial accounts, loaded again when the owner changes
  const financial = useResource(`financial-choices:${linkable ? ownerId : ''}`, (signal) =>
    linkable && ownerId !== '' ? loadFinancialChoices(ownerId, signal) : Promise.resolve([]),
  );

  const limit = limitText.trim() === '' ? { ok: true, value: null } : parse(limitText);
  const after: FinancialForm = { ...form, creditLimit: limit.value };
  const currencyOk = limit.value === null || /^[A-Z]{3}$/.test(form.currency);
  const valid = form.name.trim() !== '' && form.owner !== null && limit.ok && currencyOk;
  const body = account ? patchBody(account, before, after) : createBody(after, kind);

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
              onChange={(e) =>
                set({
                  owner: owners.find((o) => o.id === e.target.value) ?? null,
                  // a link belongs to one owner: a new owner starts without one
                  ...(linkable ? { financialId: null } : {}),
                })
              }
            >
              {form.owner === null && <option value="">Choose a party</option>}
              {owners.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name} · {o.kind === 'Individual' ? 'person' : 'organization'}
                </option>
              ))}
            </select>
          </Field>
          {account && !linkable && (
            <div className="caption">
              The owner cannot change while billing, party or settlement accounts link to this
              financial account; the server refuses it.
            </div>
          )}
          {linkable && (
            <>
              <Field label="Financial account">
                <select
                  className="bss-input"
                  data-ui={`${MARKER[kind]}.financialLink`}
                  value={form.financialId ?? ''}
                  disabled={form.owner === null || financial.status === 'loading'}
                  onChange={(e) => set({ financialId: e.target.value || null })}
                >
                  <option value="">None</option>
                  {financial.status === 'ok' &&
                    financial.data.map((choice) => (
                      <option key={choice.id} value={choice.id}>
                        {choice.name}
                        {choice.state === 'Closed' ? ' (closed)' : ''}
                      </option>
                    ))}
                  {form.financialId !== null &&
                    form.financialId !== undefined &&
                    !(
                      financial.status === 'ok' &&
                      financial.data.some((choice) => choice.id === form.financialId)
                    ) && <option value={form.financialId}>Current financial account</option>}
                </select>
              </Field>
              {financial.status === 'error' && (
                <div className="bss-inline-error">
                  Failed to load the owner's financial accounts: {explain(financial.error)}.{' '}
                  <Button small onClick={financial.retry}>
                    Retry
                  </Button>
                </div>
              )}
              <div className="caption">
                Only financial accounts of the same owner can be linked; the server refuses others.
              </div>
            </>
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
                  await patchAccount(kind, account.id, body);
                  return `${form.name.trim()} saved`;
                }
                await createAccount(kind, body);
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
  kind = 'FinancialAccount',
  onClose,
  onDone,
}: {
  account: AccountRecord;
  kind?: AccountKind;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const write = useWrite(onDone);
  return (
    <Modal
      title={`Delete ${account.name}`}
      width={480}
      onClose={onClose}
      ui={`${MARKER[kind]}.confirmDelete`}
      footer={
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            disabled={write.busy}
            onClick={() =>
              void write.run(async () => {
                await deleteAccount(kind, account.id);
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
        <p>
          Deletes the {NAME[kind]}.{' '}
          {kind === 'FinancialAccount' ? 'No account links to it.' : 'Nothing refers to it.'}
        </p>
      </div>
    </Modal>
  );
}
