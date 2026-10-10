import { useState, type ReactNode } from 'react';
import {
  createSubscriber,
  loadProfile,
  partyName,
  updateSubscriber,
  type Account,
  type AccountState,
  type CustomerStatus,
  type PartyKind,
  type SubscriberProfile,
} from '../api/bss';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { accountLabel } from '../format/ids';
import { parseAmount } from '../format/money';
import { Button, Modal, Segmented, TextInput } from '../ui/kit';
import { ErrorBlock, SkeletonRows } from '../ui/states';

// Create and edit a subscriber (party + customer + billing account) through the TMF APIs.
// There is no delete: an account carries products, bills and payments; it can be Closed.
// The backend validates everything again; the checks here only keep obviously wrong
// requests from being sent.

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="bss-field">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

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

export function NewSubscriberModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (accountId: string, name: string) => void;
}) {
  const context = useSystemContext();
  const [kind, setKind] = useState<PartyKind>('Individual');
  const [givenName, setGivenName] = useState('');
  const [familyName, setFamilyName] = useState('');
  const [organizationName, setOrganizationName] = useState('');
  const [accountName, setAccountName] = useState('');
  const [cycleDay, setCycleDay] = useState('1');
  const [limit, setLimit] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const name = partyName({ kind, givenName, familyName, organizationName });
  const named =
    kind === 'Individual'
      ? givenName.trim() !== '' && familyName.trim() !== ''
      : organizationName.trim() !== '';
  const day = cycleDayOf(cycleDay);
  const credit = limitOf(limit);
  const effectiveAccountName = accountName.trim() || `${name} main`;
  const valid = named && day !== null && credit.ok;

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
        accountName: effectiveAccountName,
        cycleDay: day,
        currency: context.currency,
        creditLimit: credit.value,
      });
      onCreated(accountId, name);
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="New subscriber"
      width={560}
      onClose={onClose}
      ui="ui.subscribers.new"
      subtitle="A party, its customer and a billing account"
      footer={
        <>
          {problem !== null && (
            <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
              {problem}
            </div>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!valid || busy} onClick={() => void submit()}>
            Create subscriber
          </Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <section>
          <Segmented label="Subscriber type" value={kind} onChange={setKind} options={KINDS} />
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
          <Field label="Account name">
            <TextInput
              placeholder={named ? `${name} main` : 'Name main'}
              value={accountName}
              onChange={(e) => setAccountName(e.target.value)}
            />
          </Field>
          <Field label="Billing cycle day (1-28)">
            <TextInput
              mono
              width={80}
              value={cycleDay}
              onChange={(e) => setCycleDay(e.target.value)}
            />
          </Field>
          <Field label={`Credit limit, ${context.currency} (optional)`}>
            <TextInput mono width={160} value={limit} onChange={(e) => setLimit(e.target.value)} />
          </Field>
          {day === null && <div className="bss-inline-error">The cycle day is 1 to 28.</div>}
          {!credit.ok && (
            <div className="bss-inline-error">Enter a credit limit with at most 2 decimals.</div>
          )}
          <div className="caption">
            Currency {context.currency}. The cycle day and the currency cannot be changed later.
          </div>
        </section>
      </div>
    </Modal>
  );
}

const CUSTOMER_STATUSES: CustomerStatus[] = ['Created', 'Active', 'Inactive'];
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
    <Modal
      title="Edit subscriber"
      width={560}
      onClose={onClose}
      ui="ui.subscriber.edit"
      subtitle={`${account.customerName} · ${accountLabel(account.id)}`}
    >
      {profile.status === 'loading' && (
        <div className="bss-modal-body">
          <SkeletonRows columns={[{ key: 'a', label: '', width: '1fr' }]} rows={5} />
        </div>
      )}
      {profile.status === 'error' && (
        <div className="bss-modal-body">
          <ErrorBlock
            title="Failed to load the subscriber"
            error={profile.error}
            onRetry={profile.retry}
          />
        </div>
      )}
      {profile.status === 'ok' && (
        <EditForm
          before={profile.data}
          currency={account.currency}
          onClose={onClose}
          onSaved={onSaved}
        />
      )}
    </Modal>
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
      <div className="bss-modal-body">
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
          {!credit.ok && (
            <div className="bss-inline-error">Enter a credit limit with at most 2 decimals.</div>
          )}
        </section>
      </div>
      <footer className="bss-modal-footer">
        {problem !== null && (
          <div className="bss-inline-error" role="alert" style={{ marginRight: 'auto' }}>
            {problem}
          </div>
        )}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!valid || !changed || busy} onClick={() => void save()}>
          Save
        </Button>
      </footer>
    </>
  );
}
