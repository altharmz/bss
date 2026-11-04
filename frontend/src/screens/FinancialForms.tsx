import { useWriteLock, writeLock } from '../api/WriteLock';
import { DialogFooter } from '../ui/kit';
import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { Children, cloneElement, isValidElement, useState } from 'react';
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
import { SelectField, Button, Modal, Segmented, TextInput } from '../ui/kit';
import { Combobox } from '../ui/combobox';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { Field } from './SubscriberForms';
import { uncertain } from './submissionKey';

// New, edit and delete of a financial account (docs/ui.md).
// The server decides: its 409/422 text is shown in the dialog and the entered values stay.
// After an uncertain result (no answer, 5xx) the dialog offers Refresh, never a retry.

const STATES: AccountState[] = ['Active', 'Suspended', 'Closed'];

// The party and settlement tabs reuse these dialogs (docs/ui.md).
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
  const { begin } = useWriteLock();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);
  const [failedCall, setFailedCall] = useState<string | undefined>();
  async function run(action: () => Promise<string>) {
    const release = begin();
    const before = writeLock.sent();
    setBusy(true);
    setProblem(null);
    setUnsure(false);
    setFailedCall(undefined);
    try {
      const message = await action();
      if (writeLock.sent() === before) setProblem('Not sent. Try again.');
      else onDone(message);
    } catch (error) {
      if (uncertain(error)) {
        setUnsure(true);
        setFailedCall(error instanceof Error ? error.message : String(error));
        setProblem(`${explain(error)}. Unsure whether it was saved: Refresh to check.`);
      } else {
        setProblem(explain(error));
      }
    } finally {
      release();
      setBusy(false);
    }
  }
  return { busy, problem, unsure, failedCall, run };
}

export function Footer({
  write,
  onRefresh,
  left,
  children,
}: {
  write: Pick<ReturnType<typeof useWrite>, 'busy' | 'problem' | 'unsure'> & {
    code?: string | undefined;
    failedCall?: string | undefined;
  };
  onRefresh: () => void;
  left?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <>
      {write.unsure ? (
        <Note
          tone="warning"
          lead="Unsure whether it was saved."
          code={write.failedCall}
          className="bss-write-note"
        >
          The request was sent but no answer came back. Refresh to read it back before trying again.
        </Note>
      ) : (
        write.problem !== null && (
          <Note tone="negative" lead="Refused." code={write.code} className="bss-write-note">
            {write.problem}
          </Note>
        )
      )}
      <div className="bss-write-actions">
        {left !== undefined && <div className="bss-write-left">{left}</div>}
        {Children.map(children, (child) => {
          if (!isValidElement<React.ComponentProps<typeof Button>>(child) || child.type !== Button)
            return child;
          const action =
            child.props.variant === 'primary' ||
            child.props.variant === 'danger' ||
            Boolean(
              child.props.onClick &&
              child.props.children !== 'Cancel' &&
              child.props.children !== 'Close',
            );
          if (write.unsure && action) return null;
          return cloneElement(child, {
            disabled: write.busy || child.props.disabled,
            ...(action
              ? {
                  busy: write.busy,
                  busyLabel: '',
                  className: `${child.props.className ?? ''} bss-write-button`.trim(),
                  children: (
                    <>
                      <span
                        className="bss-write-idle-label"
                        aria-hidden={write.busy && Boolean(child.props.busyLabel)}
                      >
                        {child.props.children}
                      </span>
                      {child.props.busyLabel && (
                        <span className="bss-write-busy-label" aria-hidden={!write.busy}>
                          {child.props.busyLabel}
                        </span>
                      )}
                    </>
                  ),
                }
              : {}),
          });
        })}
        {write.unsure && (
          <Button variant="primary" onClick={onRefresh}>
            Refresh
          </Button>
        )}
      </div>
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
    <Modal
      onSubmit={submitDialogPrimary}
      title={title}
      width={560}
      onClose={onClose}
      ui={`${MARKER[kind]}.form`}
    >
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
        <fieldset className="bss-form bss-write-fields" disabled={write.busy}>
          <Field label="Name">
            <TextInput value={form.name} onChange={(e) => set({ name: e.target.value })} />
          </Field>
          <Field label="Owner">
            <Combobox
              ui={`${MARKER[kind]}.owner`}
              placeholder="Choose a party"
              value={form.owner?.id ?? ''}
              options={owners.map((owner) => ({
                value: owner.id,
                label: `${owner.name} · ${owner.kind === 'Individual' ? 'person' : 'organization'}`,
              }))}
              onChange={(id) =>
                set({
                  owner: owners.find((o) => o.id === id) ?? null,
                  ...(linkable ? { financialId: null } : {}),
                })
              }
            />
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
                <SelectField
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
                </SelectField>
              </Field>
              {financial.status === 'error' && (
                <Note tone="negative">
                  Failed to load the owner's financial accounts: {explain(financial.error)}.{' '}
                  <Button small onClick={financial.retry}>
                    Retry
                  </Button>
                </Note>
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
          {!limit.ok && <Note tone="negative">Enter a credit limit with at most 2 decimals.</Note>}
          {!currencyOk && <Note tone="negative">The currency is 3 letters.</Note>}
          <Segmented
            label="State"
            value={form.state}
            onChange={(state) => set({ state })}
            options={STATES.map((key) => ({ key, label: key }))}
          />
        </fieldset>
      </div>
      <DialogFooter>
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
      </DialogFooter>
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
      onSubmit={submitDialogPrimary}
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
