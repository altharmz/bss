import { DialogFooter } from '../ui/kit';
import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useState } from 'react';
import {
  BUILT_IN_CYCLE,
  createBillingAccount,
  createSubscriber,
  SubscriberStepError,
  loadAccount,
  loadProfile,
  partyName,
  updateSubscriber,
  type Account,
  type AccountState,
  type BillingPrefs,
  type CustomerStatus,
  type PartyKind,
  type RatingMode,
  type SubscriberProfile,
} from '../api/bss';
import { ApiError, explain } from '../api/client';
import { loadSettings } from '../api/billing';
import { deleteBillingAccount, loadFinancialChoices } from '../api/financial';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { accountLabel } from '../format/ids';
import { parseAmount } from '../format/money';
import { Button, Checkbox, Dialog, Field, SelectField, Segmented, TextInput } from '../ui/kit';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { AddProductModal } from './AddProduct';
import { uncertain } from './submissionKey';

// Create and edit a subscriber (party + customer + billing account) through the TMF APIs.
// Delete of an account without history is on the subscriber card (SubscriberCard.tsx).
// The backend validates everything again; the checks here only keep obviously wrong
// requests from being sent.

export { Field };

/** A credit limit field: empty = none, else a non-negative amount (canonical string). */
function limitOf(text: string): { ok: boolean; value: string | null } {
  if (text.trim() === '') return { ok: true, value: null };
  const value = parseAmount(text);
  return { ok: value !== null, value };
}

function cycleDayOf(text: string): number | null {
  if (!/^\d{1,2}$/.test(text.trim())) return null;
  const day = Number(text.trim());
  return day >= 1 && day <= 28 ? day : null;
}

const KINDS: { key: PartyKind; label: string }[] = [
  { key: 'Individual', label: 'Person' },
  { key: 'Organization', label: 'Organization' },
];

const MODES: { key: RatingMode; label: string }[] = [
  { key: 'postpaid', label: 'Postpaid' },
  { key: 'prepaid', label: 'Prepaid' },
];

/** The account step shared by New subscriber and Add billing account. */
function useAccountDraft(ownerName: string) {
  const context = useSystemContext();
  const [accountName, setAccountName] = useState('');
  const [cycleDay, setCycleDay] = useState('1');
  const [limit, setLimit] = useState('');
  const [mode, setMode] = useState<RatingMode>('postpaid');
  const prepaid = mode === 'prepaid';
  const day = prepaid ? 1 : cycleDayOf(cycleDay);
  // a prepaid account has no credit: the field is hidden and nothing is sent
  const credit = prepaid ? { ok: true, value: null } : limitOf(limit);
  const fallback = `${ownerName} main`;
  return {
    currency: context.currency,
    mode,
    setMode,
    accountName,
    setAccountName,
    cycleDay,
    setCycleDay,
    limit,
    setLimit,
    prepaid,
    day,
    credit,
    placeholder: ownerName === '' ? 'Name main' : fallback,
    effectiveName: accountName.trim() || fallback,
    valid: day !== null && credit.ok,
  };
}

type AccountDraft = ReturnType<typeof useAccountDraft>;

function AccountFields({ draft }: { draft: AccountDraft }) {
  const { currency, prepaid } = draft;
  return (
    <>
      <Field label="Account name">
        <TextInput
          placeholder={draft.placeholder}
          value={draft.accountName}
          onChange={(e) => draft.setAccountName(e.target.value)}
        />
      </Field>
      {!prepaid && (
        <Field label="Billing cycle day (1-28)">
          <TextInput
            mono
            width={80}
            value={draft.cycleDay}
            onChange={(e) => draft.setCycleDay(e.target.value)}
          />
        </Field>
      )}
      {!prepaid && (
        <Field label={`Credit limit, ${currency} (optional)`}>
          <TextInput
            mono
            width={160}
            value={draft.limit}
            onChange={(e) => draft.setLimit(e.target.value)}
          />
        </Field>
      )}
      {draft.day === null && <Note tone="negative">The cycle day is 1 to 28.</Note>}
      {!draft.credit.ok && (
        <Note tone="negative">Enter a credit limit with at most 2 decimals.</Note>
      )}
      <div className="caption">
        Currency {currency}.{' '}
        {prepaid
          ? 'The currency cannot be changed later.'
          : 'The cycle day and the currency cannot be changed later.'}
        {prepaid && ' A prepaid account spends only funds it holds; it takes no credit limit.'}
      </div>
    </>
  );
}

/** A failed create: the message, and a link to Parties when records were left behind. */
function StepProblem({ message, error }: { message: string; error: unknown }) {
  return (
    <Note tone="negative" className="footer-push">
      {message}
      {error instanceof SubscriberStepError && error.leftovers.length > 0 && (
        <>
          {' '}
          <Link className="bss-link" href={hrefOf({ name: 'parties' })}>
            Open Parties
          </Link>
        </>
      )}
    </Note>
  );
}

export function NewSubscriberModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (accountId: string, name: string) => void;
}) {
  const [kind, setKind] = useState<PartyKind>('Individual');
  const [givenName, setGivenName] = useState('');
  const [familyName, setFamilyName] = useState('');
  const [organizationName, setOrganizationName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ message: string; error: unknown } | null>(null);
  const [initialProduct, setInitialProduct] = useState(false);
  const [createdId, setCreatedId] = useState<string | null>(null);

  const name = partyName({ kind, givenName, familyName, organizationName });
  const named =
    kind === 'Individual'
      ? givenName.trim() !== '' && familyName.trim() !== ''
      : organizationName.trim() !== '';
  const draft = useAccountDraft(named ? name : '');
  const { day, credit } = draft;
  const valid = named && draft.valid;

  async function submit() {
    if (!valid || day === null) return;
    setBusy(true);
    setProblem(null);
    try {
      const accountId = await createSubscriber({
        kind,
        givenName,
        familyName,
        organizationName,
        email,
        phone,
        accountName: draft.effectiveName,
        cycleDay: day,
        currency: draft.currency,
        creditLimit: credit.value,
        ratingType: draft.mode,
      });
      if (initialProduct) setCreatedId(accountId);
      else onCreated(accountId, name);
    } catch (error) {
      setProblem({ message: explain(error), error });
    } finally {
      setBusy(false);
    }
  }

  if (createdId)
    return <InitialProduct accountId={createdId} onDone={() => onCreated(createdId, name)} />;

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="New subscriber"
      size="M"
      onClose={onClose}
      ui="ui.subscribers.new"
      context="A party, its customer and a billing account"
      footer={
        <>
          {problem !== null && <StepProblem {...problem} />}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!valid || busy} onClick={() => void submit()}>
            Create subscriber
          </Button>
        </>
      }
    >
      <>
        <section>
          <Segmented
            as="radio"
            label="Subscriber type"
            value={kind}
            onChange={setKind}
            options={KINDS}
          />
          <Segmented
            as="radio"
            label="Account mode"
            value={draft.mode}
            onChange={draft.setMode}
            options={MODES}
          />
        </section>
        <section className="bss-form">
          {kind === 'Individual' ? (
            <>
              <Field label="Given name">
                <TextInput value={givenName} onChange={(e) => setGivenName(e.target.value)} />
              </Field>
              <Field label="Family name">
                <TextInput value={familyName} onChange={(e) => setFamilyName(e.target.value)} />
              </Field>
            </>
          ) : (
            <Field label="Organization name">
              <TextInput
                value={organizationName}
                onChange={(e) => setOrganizationName(e.target.value)}
              />
            </Field>
          )}
          <div data-ui="ui.subscribers.contactMedia">
            <Field label="Email">
              <TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Field label="Phone">
              <TextInput type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </Field>
          </div>
          <AccountFields draft={draft} />
          <Checkbox
            checked={initialProduct}
            onChange={(e) => setInitialProduct(e.target.checked)}
            label="After creating the account, add a product. Offerings that need a subscriber number ask for one."
          />
        </section>
      </>
    </Dialog>
  );
}

/** One more billing account for an existing customer: the account step of New subscriber. */
export function AddBillingAccountModal({
  customer,
  onClose,
  onCreated,
}: {
  customer: { id: string; name: string };
  onClose: () => void;
  onCreated: (accountId: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const draft = useAccountDraft(customer.name);
  const { day, credit } = draft;

  async function submit() {
    if (!draft.valid || day === null) return;
    setBusy(true);
    setProblem(null);
    try {
      onCreated(
        await createBillingAccount(customer, {
          accountName: draft.effectiveName,
          cycleDay: day,
          currency: draft.currency,
          creditLimit: credit.value,
          ratingType: draft.mode,
        }),
      );
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Add billing account"
      size="M"
      onClose={onClose}
      ui="ui.account.new"
      context={`Customer ${customer.name}`}
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!draft.valid || busy} onClick={() => void submit()}>
            Add billing account
          </Button>
        </>
      }
    >
      <>
        <section>
          <Segmented
            as="radio"
            label="Account mode"
            value={draft.mode}
            onChange={draft.setMode}
            options={MODES}
          />
        </section>
        <section className="bss-form">
          <AccountFields draft={draft} />
        </section>
      </>
    </Dialog>
  );
}

function InitialProduct({ accountId, onDone }: { accountId: string; onDone: () => void }) {
  const account = useResource(`new-account:${accountId}`, (signal) =>
    loadAccount(accountId, signal),
  );
  if (account.status === 'ok')
    return (
      <AddProductModal
        account={account.data}
        activeOfferingIds={new Set()}
        onClose={onDone}
        onOrdered={onDone}
      />
    );
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Subscriber created"
      size="M"
      onClose={onDone}
      footer={<Button onClick={onDone}>Open subscriber</Button>}
    >
      <>
        <p>The account was created. Add its initial product next.</p>
        {account.status === 'error' && (
          <ErrorBlock
            title="Failed to load the created account"
            error={account.error}
            onRetry={account.retry}
          />
        )}
      </>
    </Dialog>
  );
}

export const CUSTOMER_STATUSES: CustomerStatus[] = ['Created', 'Active', 'Inactive'];
const ACCOUNT_STATES: AccountState[] = ['Active', 'Suspended', 'Closed'];

export function EditSubscriberModal({
  account,
  onClose,
  onSaved,
}: {
  account: Account;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const profile = useResource(`profile:${account.id}`, (signal) => loadProfile(account, signal));
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Edit subscriber"
      size="M"
      onClose={onClose}
      ui="ui.subscriber.edit"
      context={`${account.customerName} · ${accountLabel(account.id)}`}
    >
      {profile.status === 'loading' && (
        <>
          <SkeletonRows columns={[{ key: 'a', label: '', width: '1fr' }]} rows={5} />
        </>
      )}
      {profile.status === 'error' && (
        <>
          <ErrorBlock
            title="Failed to load the subscriber"
            error={profile.error}
            onRetry={profile.retry}
          />
        </>
      )}
      {profile.status === 'ok' && (
        <EditForm
          before={profile.data}
          currency={account.currency}
          onClose={onClose}
          onSaved={onSaved}
        />
      )}
    </Dialog>
  );
}

function EditForm({
  before,
  currency,
  onClose,
  onSaved,
}: {
  before: SubscriberProfile;
  currency: string;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [form, setForm] = useState(before);
  const [limitText, setLimitText] = useState(before.creditLimit ?? '');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const set = (change: Partial<SubscriberProfile>) => setForm({ ...form, ...change });
  // the owner's financial accounts: the party engaged by the customer
  const financial = useResource(`financial-choices:${before.partyId}`, (signal) =>
    loadFinancialChoices(before.partyId, signal),
  );

  // the bill structure choices (slice 18): a failed list leaves its select disabled
  const cycles = useResource('billing-settings:cycles', (signal) => loadSettings('cycles', signal));
  const formats = useResource('billing-settings:formats', (signal) =>
    loadSettings('formats', signal),
  );
  const media = useResource('billing-settings:media', (signal) => loadSettings('media', signal));
  const setBilling = (change: Partial<BillingPrefs>) =>
    set({ billing: { ...form.billing, ...change } });

  const credit = limitOf(limitText);
  const after: SubscriberProfile = { ...form, creditLimit: credit.value };
  const named =
    form.kind === 'Individual'
      ? form.givenName.trim() !== '' && form.familyName.trim() !== ''
      : form.organizationName.trim() !== '';
  const valid =
    named && form.customerName.trim() !== '' && form.accountName.trim() !== '' && credit.ok;
  const changed = JSON.stringify(after) !== JSON.stringify(before);

  async function save() {
    if (!valid || !changed) return;
    setBusy(true);
    setProblem(null);
    try {
      const saved = await updateSubscriber(before, after, currency);
      onSaved(`Saved: ${saved.join(', ')}`);
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <>
        <section className="bss-form">
          <div className="bss-step-head">
            {form.kind === 'Individual' ? 'Person' : 'Organization'}
          </div>
          {form.kind === 'Individual' ? (
            <>
              <Field label="Given name">
                <TextInput
                  value={form.givenName}
                  onChange={(e) => set({ givenName: e.target.value })}
                />
              </Field>
              <Field label="Family name">
                <TextInput
                  value={form.familyName}
                  onChange={(e) => set({ familyName: e.target.value })}
                />
              </Field>
            </>
          ) : (
            <Field label="Organization name">
              <TextInput
                value={form.organizationName}
                onChange={(e) => set({ organizationName: e.target.value })}
              />
            </Field>
          )}
        </section>
        <section className="bss-form">
          <div className="bss-step-head">Customer</div>
          <Field label="Customer name">
            <TextInput
              value={form.customerName}
              onChange={(e) => set({ customerName: e.target.value })}
            />
          </Field>
          <Segmented
            label="Customer status"
            value={form.customerStatus}
            onChange={(customerStatus) => set({ customerStatus })}
            options={CUSTOMER_STATUSES.map((key) => ({ key, label: key }))}
          />
        </section>
        <section className="bss-form">
          <div className="bss-step-head">Billing account</div>
          <Field label="Account name">
            <TextInput
              value={form.accountName}
              onChange={(e) => set({ accountName: e.target.value })}
            />
          </Field>
          <Segmented
            label="Account state"
            value={form.accountState}
            onChange={(accountState) => set({ accountState })}
            options={ACCOUNT_STATES.map((key) => ({ key, label: key }))}
          />
          {form.accountState !== 'Active' && form.accountState !== before.accountState && (
            <div className="caption">
              A {form.accountState.toLowerCase()} account takes no new orders; its products keep
              billing until they are deactivated.
            </div>
          )}
          <Field label={`Credit limit, ${currency} (empty = none)`}>
            <TextInput
              mono
              width={160}
              value={limitText}
              onChange={(e) => setLimitText(e.target.value)}
            />
          </Field>
          {!credit.ok && <Note tone="negative">Enter a credit limit with at most 2 decimals.</Note>}
          <Field label="Financial account">
            <SelectField
              data-ui="ui.subscriber.financialLink"
              value={form.financialAccountId ?? ''}
              disabled={financial.status === 'loading'}
              onChange={(e) => set({ financialAccountId: e.target.value || null })}
            >
              <option value="">None</option>
              {financial.status === 'ok' &&
                financial.data.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.name}
                    {choice.state === 'Closed' ? ' (closed)' : ''}
                  </option>
                ))}
              {form.financialAccountId !== null &&
                !(
                  financial.status === 'ok' &&
                  financial.data.some((choice) => choice.id === form.financialAccountId)
                ) && <option value={form.financialAccountId}>Current financial account</option>}
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
        </section>
        <section className="bss-form" data-ui="ui.subscriber.billingGroup">
          <div className="bss-step-head">Billing</div>
          <Field label="Cycle specification">
            <SelectField
              data-ui="ui.subscriber.cycleSpecification"
              value={form.billing.cycleId ?? BUILT_IN_CYCLE}
              disabled={cycles.status !== 'ok'}
              onChange={(e) =>
                setBilling({ cycleId: e.target.value === BUILT_IN_CYCLE ? null : e.target.value })
              }
            >
              {cycles.status === 'ok' ? (
                cycles.data.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.name}
                  </option>
                ))
              ) : (
                <option value={form.billing.cycleId ?? BUILT_IN_CYCLE}>Current</option>
              )}
            </SelectField>
          </Field>
          <Field label="Bill format">
            <SelectField
              data-ui="ui.subscriber.billFormat"
              value={form.billing.formatId ?? ''}
              disabled={formats.status !== 'ok'}
              onChange={(e) => setBilling({ formatId: e.target.value || null })}
            >
              <option value="">Default</option>
              {formats.status === 'ok' ? (
                formats.data.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choice.name}
                  </option>
                ))
              ) : form.billing.formatId !== null ? (
                <option value={form.billing.formatId}>Current</option>
              ) : null}
            </SelectField>
          </Field>
          <fieldset aria-label="Presentation media" data-ui="ui.subscriber.presentationMedia">
            <legend className="label">Presentation media</legend>
            {media.status === 'ok' && media.data.length === 0 && (
              <span className="caption">None defined yet (Billing settings).</span>
            )}
            {media.status === 'ok' &&
              media.data.map((choice) => (
                <Checkbox
                  key={choice.id}
                  checked={form.billing.mediaIds.includes(choice.id)}
                  onChange={(e) =>
                    setBilling({
                      mediaIds: e.target.checked
                        ? [...form.billing.mediaIds, choice.id]
                        : form.billing.mediaIds.filter((id) => id !== choice.id),
                    })
                  }
                  label={choice.name}
                />
              ))}
          </fieldset>
          {(cycles.status === 'error' ||
            formats.status === 'error' ||
            media.status === 'error') && (
            <Note tone="negative">
              Failed to load the billing settings.{' '}
              <Button
                small
                onClick={() => {
                  cycles.retry();
                  formats.retry();
                  media.retry();
                }}
              >
                Retry
              </Button>
            </Note>
          )}
          <div className="caption">
            Cycle specifications, formats and media are managed under Billing settings.
          </div>
        </section>
      </>
      <DialogFooter>
        {problem !== null && (
          <Note tone="negative" className="footer-push">
            {problem}
          </Note>
        )}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!valid || !changed || busy} onClick={() => void save()}>
          Save
        </Button>
      </DialogFooter>
    </>
  );
}

/** Delete a billing account without history. The card offers it only when everything it
 * loaded is empty; a 409 from the server (history it did not show) says to close it instead. */
export function DeleteSubscriberModal({
  account,
  onClose,
  onDeleted,
}: {
  account: Account;
  onClose: () => void;
  onDeleted: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);

  async function remove() {
    setBusy(true);
    setProblem(null);
    setUnsure(false);
    try {
      await deleteBillingAccount(account.id);
      onDeleted(`${account.name} deleted`);
    } catch (error) {
      if (uncertain(error)) {
        setUnsure(true);
        setProblem(`${explain(error)}. Unsure whether it was saved: Refresh to check.`);
      } else if (error instanceof ApiError && error.status === 409) {
        setProblem(`${explain(error)} - close it instead.`);
      } else {
        setProblem(explain(error));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Delete billing account"
      size="M"
      onClose={onClose}
      ui="ui.subscriber.deleteConfirm"
      context={`${account.customerName} · ${accountLabel(account.id)}`}
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          {unsure && <Button onClick={() => onDeleted('Refreshed')}>Refresh</Button>}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" disabled={busy} onClick={() => void remove()}>
            Delete
          </Button>
        </>
      }
    >
      <>
        <p>Deletes the billing account. Its customer and party stay (delete them under Parties).</p>
      </>
    </Dialog>
  );
}
