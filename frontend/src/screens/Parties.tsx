import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useRef, useState, type ReactNode } from 'react';
import type { CustomerStatus, PartyKind } from '../api/bss';
import { deleteBillingAccount } from '../api/financial';
import { explain } from '../api/client';
import {
  createCustomer,
  createParty,
  customerChanges,
  deleteCustomer,
  deleteParty,
  loadCustomerDetail,
  loadPartyDetail,
  loadPartyPage,
  PARTY_PAGE,
  PARTY_STATUSES,
  partyBlock,
  partyChanges,
  patchCustomer,
  patchParty,
  type CustomerDetail,
  type NewParty,
  type PartyCustomer,
  type PartyDetail,
  type PartyFilter,
  type PartyRow,
} from '../api/parties';
import { EMPTY_CONTACT, type ContactFields } from '../api/partyDocuments';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { zonedDateTime } from '../format/dates';
import { shortId } from '../format/ids';
import {
  Button,
  DateField,
  Dialog,
  KeyValue,
  PageHeader,
  StatusPill,
  Segmented,
  TextInput,
  useToast,
} from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { PageFooter, usePageOffset, useSettled } from '../ui/paging';
import { AddBillingAccountModal, CUSTOMER_STATUSES, Field } from './SubscriberForms';
import { AccountPinField, AuditLog } from './SecurityTab';
import { uncertain } from './submissionKey';
import './screens.css';

// Individuals and organizations, whether or not they have a billing account. Parties and
// their customers can be created here. A party row opens the record (GET) with its billing
// account links and an edit for its identity, status and documents; Delete is offered
// unless the loaded data already shows a dependency, and the server's 409 still shows.
// The customer list is its own screen (Customers.tsx, #457) and shares the customer dialog.
// Design: docs/ui.md.

type Tab = 'individuals' | 'organizations';

const PARTY_COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'status', label: 'Status', width: '130px' },
  { key: 'customers', label: 'Customers', width: '110px', align: 'right', mono: true },
  { key: 'accounts', label: 'Billing accounts', width: '1fr' },
];

const dash = <span className="bss-dash">—</span>;

export function AccountLinks({ links }: { links: { id: string; name: string }[] }) {
  if (links.length === 0) return dash;
  return (
    <div className="ellipsis" onClick={(event) => event.stopPropagation()}>
      {links.map((account, i) => (
        <span key={account.id}>
          {i > 0 && ', '}
          <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId: account.id })}>
            {account.name}
          </Link>
        </span>
      ))}
    </div>
  );
}

export function PartiesScreen() {
  useSetCrumbs([{ label: 'Parties' }], 'parties');
  const toast = useToast();
  const [version, setVersion] = useState(0);
  const [tab, setTab] = useState<Tab>('individuals');
  const [query, setQuery] = useState('');
  const q = useSettled(query);
  const [status, setStatus] = useState('All');
  const [open, setOpen] = useState<PartyRow | null>(null);
  const [creating, setCreating] = useState<'Individual' | 'Organization' | null>(null);

  const statuses = PARTY_STATUSES[tab === 'individuals' ? 'Individual' : 'Organization'];
  const filter = { q, status: status === 'All' ? null : status };
  const filterKey = `${tab}:${q}:${status}:${version}`;
  const [offset, setOffset] = usePageOffset(filterKey);
  const filtered = query !== '' || status !== 'All';
  const changeTab = (next: Tab) => {
    setTab(next);
    setStatus('All');
  };
  const reset = () => {
    setQuery('');
    setStatus('All');
  };
  const done = (message: string) => {
    setOpen(null);
    setCreating(null);
    toast(message);
    setVersion((v) => v + 1);
  };
  const paging = { filterKey, offset, setOffset, filter, filtered, reset };

  return (
    <div className="bss-screen" data-ui="ui.parties">
      <PageHeader
        title="Parties"
        actions={
          <>
            <Button
              data-ui="ui.parties.newOrganization"
              onClick={() => setCreating('Organization')}
            >
              New organization
            </Button>
            <Button
              variant="primary"
              data-ui="ui.parties.newIndividual"
              onClick={() => setCreating('Individual')}
            >
              New individual
            </Button>
          </>
        }
        filters={
          <>
            <Segmented
              label="Party sections"
              value={tab}
              onChange={changeTab}
              options={[
                { key: 'individuals', label: 'Individuals' },
                { key: 'organizations', label: 'Organizations' },
              ]}
            />
            <TextInput
              width={300}
              placeholder="Name or id (starts with)"
              aria-label={`Search ${tab}`}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Segmented
              label="Status"
              value={status}
              onChange={setStatus}
              options={['All', ...statuses].map((key) => ({ key, label: key }))}
            />
          </>
        }
      />
      <div role="table" aria-label="Parties" className="bss-page-table">
        <div data-ui={`ui.parties.${tab}`}>
          <PartiesList
            {...paging}
            kind={tab === 'individuals' ? 'Individual' : 'Organization'}
            tab={tab}
            onOpen={setOpen}
          />
        </div>
      </div>
      {open !== null && <PartyModal party={open} onClose={() => setOpen(null)} onDone={done} />}
      {creating !== null && (
        <NewPartyModal kind={creating} onClose={() => setCreating(null)} onDone={done} />
      )}
    </div>
  );
}

export interface Paging {
  filterKey: string;
  offset: number;
  setOffset: (offset: number) => void;
  filter: PartyFilter;
  filtered: boolean;
  reset: () => void;
}

function PartiesList({
  filterKey,
  offset,
  setOffset,
  filter,
  filtered,
  reset,
  kind,
  tab,
  onOpen,
}: Paging & { kind: PartyKind; tab: Tab; onOpen: (party: PartyRow) => void }) {
  const resource = useResource(`parties:${filterKey}:${offset}`, (signal) =>
    loadPartyPage(kind, filter, offset, signal),
  );
  return (
    <>
      <TableHead columns={PARTY_COLUMNS} />
      <ListState
        resource={resource}
        columns={PARTY_COLUMNS}
        errorTitle="Failed to load parties"
        empty={{ title: `No ${tab} yet`, text: 'A new billing account creates its party.' }}
        // filtered on the server: an empty filtered page is "no matches"
        count={(d) => (filtered ? 1 : d.items.length)}
        rows={(d) => d.items}
        query={filter.q || undefined}
        onResetFilters={reset}
      >
        {(rows, d) => (
          <>
            {rows.map((party) => (
              <TableRow
                key={party.id}
                columns={PARTY_COLUMNS}
                onClick={() => onOpen(party)}
                cells={{
                  name: <span className="bss-name ellipsis">{party.name}</span>,
                  status: party.status ? <StatusPill kind="party" status={party.status} /> : dash,
                  customers: (
                    <span className={party.customers.length ? '' : 'bss-zero'}>
                      {party.customers.length}
                    </span>
                  ),
                  accounts: <AccountLinks links={party.accounts} />,
                }}
              />
            ))}
            <PageFooter
              offset={d.offset}
              shown={rows.length}
              more={d.more}
              size={PARTY_PAGE}
              onOffset={setOffset}
              ui="ui.parties"
              note="a party without customers can be deleted"
            />
          </>
        )}
      </ListState>
    </>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return <KeyValue items={rows} />;
}

/** Save and Delete share one busy/problem/unsure state and the dialog frame. */
function useWrite(onDone: (message: string) => void) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [unsure, setUnsure] = useState(false);
  async function run(action: () => Promise<string>, onFail?: () => void) {
    setBusy(true);
    setProblem(null);
    setUnsure(false);
    try {
      onDone(await action());
    } catch (error) {
      onFail?.();
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
  return { busy, problem, unsure, run, clear: () => setProblem(null) };
}

interface FrameProps {
  title: string;
  ui: string;
  subtitle: ReactNode;
  state: 'loading' | 'error' | 'ok';
  error?: Error;
  retry: () => void;
  body: ReactNode;
  write: ReturnType<typeof useWrite>;
  actions: ReactNode;
  onClose: () => void;
  onRefresh: () => void;
}

function Frame(p: FrameProps) {
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={p.title}
      size="M"
      onClose={p.onClose}
      ui={p.ui}
      context={p.subtitle}
      footer={
        <>
          {p.write.problem !== null && (
            <Note tone="negative" className="footer-push">
              {p.write.problem}
            </Note>
          )}
          {p.write.unsure && <Button onClick={p.onRefresh}>Refresh</Button>}
          {p.actions}
        </>
      }
    >
      {p.state === 'loading' && (
        <SkeletonRows columns={[{ key: 'a', label: '', width: '1fr' }]} rows={4} />
      )}
      {p.state === 'error' && p.error && (
        <ErrorBlock title="Failed to load the record" error={p.error} onRetry={p.retry} />
      )}
      {p.state === 'ok' && p.body}
    </Dialog>
  );
}

function DeleteControl({
  noun,
  block,
  confirming,
  setConfirming,
  busy,
  onDelete,
}: {
  noun: string;
  block: string | null;
  confirming: boolean;
  setConfirming: (value: boolean) => void;
  busy: boolean;
  onDelete: () => void;
}) {
  if (block !== null)
    return (
      <Note tone="info" className="footer-push">
        {block}: remove {noun === 'party' ? 'them' : 'those'} first to delete it.
      </Note>
    );
  if (confirming)
    return (
      <span className="footer-push bss-confirm">
        <span className="ask">Delete this {noun}?</span>
        <Button variant="danger" small disabled={busy} onClick={onDelete}>
          Yes
        </Button>
        <Button small disabled={busy} onClick={() => setConfirming(false)}>
          No
        </Button>
      </span>
    );
  return (
    <Button
      variant="quiet"
      tone="negative"
      data-ui="ui.parties.delete"
      onClick={() => setConfirming(true)}
    >
      Delete
    </Button>
  );
}

function ContactFieldsForm({
  value,
  onChange,
  ui,
}: {
  value: ContactFields;
  onChange: (change: Partial<ContactFields>) => void;
  ui: string;
}) {
  return (
    <section className="bss-form" data-ui={ui}>
      <Field label="Email">
        <TextInput
          type="email"
          value={value.email}
          onChange={(e) => onChange({ email: e.target.value })}
        />
      </Field>
      <Field label="Phone">
        <TextInput
          type="tel"
          value={value.phone}
          onChange={(e) => onChange({ phone: e.target.value })}
        />
      </Field>
      <Field label="Street address">
        <TextInput value={value.street1} onChange={(e) => onChange({ street1: e.target.value })} />
      </Field>
      <Field label="City">
        <TextInput value={value.city} onChange={(e) => onChange({ city: e.target.value })} />
      </Field>
      <Field label="Postal code">
        <TextInput
          value={value.postCode}
          onChange={(e) => onChange({ postCode: e.target.value })}
        />
      </Field>
      <Field label="Country">
        <TextInput value={value.country} onChange={(e) => onChange({ country: e.target.value })} />
      </Field>
    </section>
  );
}

function NewPartyModal({
  kind,
  onClose,
  onDone,
}: {
  kind: 'Individual' | 'Organization';
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const context = useSystemContext();
  const write = useWrite(onDone);
  const [form, setForm] = useState<NewParty>({
    ...EMPTY_CONTACT,
    givenName: '',
    familyName: '',
    organizationName: '',
    status: PARTY_STATUSES[kind][0] ?? 'initialized',
    birthDate: null,
  });
  const set = (change: Partial<NewParty>) => setForm((current) => ({ ...current, ...change }));
  const name =
    kind === 'Individual'
      ? `${form.givenName.trim()} ${form.familyName.trim()}`
      : form.organizationName.trim();
  const valid =
    kind === 'Individual'
      ? form.givenName.trim() !== '' && form.familyName.trim() !== ''
      : form.organizationName.trim() !== '';
  const title = kind === 'Individual' ? 'New individual' : 'New organization';
  return (
    <Frame
      title={title}
      ui={`${kind === 'Individual' ? 'ui.parties.newIndividual' : 'ui.parties.newOrganization'}Form`}
      subtitle="Contact details are optional"
      state="ok"
      retry={() => undefined}
      body={
        <>
          <section className="bss-form">
            {kind === 'Individual' ? (
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
                <Field label="Birth date">
                  <DateField
                    value={form.birthDate ?? ''}
                    onChange={(e) => set({ birthDate: e.target.value })}
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
            <Segmented
              label="Party status"
              value={form.status}
              onChange={(status) => set({ status })}
              options={PARTY_STATUSES[kind].map((key) => ({ key, label: key }))}
            />
          </section>
          <ContactFieldsForm value={form} onChange={set} ui="ui.parties.contactMedia" />
        </>
      }
      write={write}
      onClose={onClose}
      onRefresh={() => onDone('Refreshed')}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!valid || write.busy}
            onClick={() =>
              void write.run(async () => {
                await createParty(kind, {
                  ...form,
                  birthDate: form.birthDate
                    ? zonedDateTime(form.birthDate, '00:00', context.timeZone)
                    : null,
                });
                return `${name} created`;
              })
            }
          >
            Create
          </Button>
        </>
      }
    />
  );
}

function NewCustomerModal({
  party,
  onClose,
  onDone,
}: {
  party: PartyRow;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const write = useWrite(onDone);
  const [name, setName] = useState(party.name);
  return (
    <Frame
      title="New customer"
      ui="ui.parties.newCustomerForm"
      subtitle={`${party.name} · ${shortId(party.id)}`}
      state="ok"
      retry={() => undefined}
      body={
        <section className="bss-form">
          <Field label="Customer name">
            <TextInput value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="caption">The customer starts Active and engages this party.</div>
        </section>
      }
      write={write}
      onClose={onClose}
      onRefresh={() => onDone('Refreshed')}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={name.trim() === '' || write.busy}
            onClick={() =>
              void write.run(async () => {
                await createCustomer(party, name);
                return `${name.trim()} created`;
              })
            }
          >
            Create customer
          </Button>
        </>
      }
    />
  );
}

function PartyModal({
  party,
  onClose,
  onDone,
}: {
  party: PartyRow;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const context = useSystemContext();
  const detail = useResource(`party:${party.kind}:${party.id}`, (signal) =>
    loadPartyDetail(party.kind, party.id, context.timeZone, signal),
  );
  const write = useWrite(onDone);
  const [editing, setEditing] = useState<PartyDetail | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [newCustomer, setNewCustomer] = useState(false);
  const [accountPin, setAccountPin] = useState('');
  const own = party.customers;
  const accounts = party.accounts;
  const before = detail.status === 'ok' ? detail.data : null;
  const changes = before && editing ? partyChanges(before, editing, context.timeZone) : null;
  const named =
    editing !== null &&
    (editing.kind === 'Individual'
      ? editing.givenName.trim() !== '' && editing.familyName.trim() !== ''
      : editing.organizationName.trim() !== '');
  const documentsValid =
    editing !== null &&
    Boolean(editing.identificationType.trim()) === Boolean(editing.identificationId.trim()) &&
    Boolean(editing.externalType.trim()) === Boolean(editing.externalId.trim());
  const set = (change: Partial<PartyDetail>) =>
    setEditing((e) => (e === null ? e : { ...e, ...change }));
  const label = party.kind === 'Individual' ? 'person' : 'organization';

  if (newCustomer)
    return <NewCustomerModal party={party} onClose={() => setNewCustomer(false)} onDone={onDone} />;

  const body =
    before === null ? null : editing !== null ? (
      <>
        <section className="bss-form">
          {editing.kind === 'Individual' ? (
            <>
              <Field label="Given name">
                <TextInput
                  value={editing.givenName}
                  onChange={(e) => set({ givenName: e.target.value })}
                />
              </Field>
              <Field label="Family name">
                <TextInput
                  value={editing.familyName}
                  onChange={(e) => set({ familyName: e.target.value })}
                />
              </Field>
              <Field label="Birth date">
                <DateField
                  value={editing.birthDate}
                  onChange={(e) => set({ birthDate: e.target.value })}
                />
              </Field>
            </>
          ) : (
            <Field label="Organization name">
              <TextInput
                value={editing.organizationName}
                onChange={(e) => set({ organizationName: e.target.value })}
              />
            </Field>
          )}
          <Segmented
            label="Party status"
            value={editing.status}
            onChange={(status) => set({ status })}
            options={PARTY_STATUSES[editing.kind].map((key) => ({ key, label: key }))}
          />
        </section>
        <ContactFieldsForm value={editing} onChange={set} ui="ui.parties.contactMedia" />
        {changes?.contactMedium !== undefined && (
          <section className="bss-form">
            <AccountPinField
              value={accountPin}
              onChange={setAccountPin}
              help="A contact or address change of a customer with a live prepaid line needs the customer's account PIN (6 to 8 digits)."
            />
          </section>
        )}
        <section className="bss-form" data-ui="ui.parties.identification">
          <Field label="Identification type">
            <TextInput
              value={editing.identificationType}
              onChange={(e) => set({ identificationType: e.target.value })}
            />
          </Field>
          <Field label="Identification ID">
            <TextInput
              value={editing.identificationId}
              onChange={(e) => set({ identificationId: e.target.value })}
            />
          </Field>
        </section>
        <section className="bss-form" data-ui="ui.parties.externalReference">
          <Field label="External reference ID">
            <TextInput
              value={editing.externalId}
              onChange={(e) => set({ externalId: e.target.value })}
            />
          </Field>
          <Field label="External reference type">
            <TextInput
              value={editing.externalType}
              onChange={(e) => set({ externalType: e.target.value })}
            />
          </Field>
          {!documentsValid && (
            <Note tone="negative">Complete both the document type and its ID.</Note>
          )}
        </section>
      </>
    ) : (
      <Facts
        rows={[
          ['Type', party.kind === 'Individual' ? 'Person' : 'Organization'],
          ...(before.kind === 'Individual'
            ? ([
                ['Given name', before.givenName],
                ['Family name', before.familyName],
                ...(before.birthDate
                  ? ([['Birth date', before.birthDate]] as [string, ReactNode][])
                  : []),
              ] as [string, ReactNode][])
            : ([['Organization name', before.organizationName]] as [string, ReactNode][])),
          ['Status', before.status ? <StatusPill kind="party" status={before.status} /> : dash],
          ['Email', before.email || dash],
          ['Phone', before.phone || dash],
          ['Customers', own.length ? own.map((c) => c.name).join(', ') : dash],
          ['Billing accounts', <AccountLinks key="s" links={accounts} />],
        ]}
      />
    );

  return (
    <Frame
      title={party.name}
      ui={`ui.parties.${party.kind === 'Individual' ? 'individual' : 'organization'}`}
      subtitle={`${label} · ${shortId(party.id)}`}
      state={detail.status}
      {...(detail.status === 'error' ? { error: detail.error } : {})}
      retry={detail.retry}
      body={body}
      write={write}
      onClose={onClose}
      onRefresh={onDone.bind(null, 'Refreshed')}
      actions={
        editing === null ? (
          <>
            <DeleteControl
              noun="party"
              block={partyBlock(party)}
              confirming={confirming}
              setConfirming={setConfirming}
              busy={write.busy}
              onDelete={() =>
                void write.run(
                  async () => {
                    await deleteParty(party.kind, party.id);
                    return `${party.name} deleted`;
                  },
                  () => setConfirming(false),
                )
              }
            />
            <Button onClick={onClose}>Close</Button>
            <Button
              data-ui="ui.parties.newCustomer"
              disabled={before === null}
              onClick={() => setNewCustomer(true)}
            >
              New customer
            </Button>
            <Button
              variant="primary"
              data-ui="ui.parties.edit"
              disabled={before === null}
              onClick={() => before && setEditing(before)}
            >
              Edit
            </Button>
          </>
        ) : (
          <>
            <Button
              onClick={() => {
                setEditing(null);
                setAccountPin('');
                write.clear();
              }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!named || !documentsValid || changes === null || write.busy}
              onClick={() =>
                changes &&
                void write.run(async () => {
                  await patchParty(
                    party.kind,
                    party.id,
                    changes,
                    changes.contactMedium === undefined ? '' : accountPin,
                  );
                  setAccountPin('');
                  return `${party.name} saved`;
                })
              }
            >
              Save
            </Button>
          </>
        )
      }
    />
  );
}

/** The customer's billing accounts, each deletable in place; a server 409 shows on its row. */
function CustomerAccounts({
  links,
  onDone,
}: {
  links: { id: string; name: string }[];
  onDone: (message: string) => void;
}) {
  if (links.length === 0) return dash;
  return (
    <ul className="bss-list" data-ui="ui.parties.customerAccounts">
      {links.map((account) => (
        <CustomerAccountRow key={account.id} account={account} onDone={onDone} />
      ))}
    </ul>
  );
}

function CustomerAccountRow({
  account,
  onDone,
}: {
  account: { id: string; name: string };
  onDone: (message: string) => void;
}) {
  const write = useWrite(onDone);
  const [confirming, setConfirming] = useState(false);
  return (
    <li>
      <Link className="bss-link" href={hrefOf({ name: 'subscriber', accountId: account.id })}>
        {account.name}
      </Link>{' '}
      {confirming ? (
        <span className="bss-confirm">
          <span className="ask">Delete this billing account?</span>
          <Button
            variant="danger"
            small
            disabled={write.busy}
            onClick={() =>
              void write.run(
                async () => {
                  await deleteBillingAccount(account.id);
                  return `${account.name} deleted`;
                },
                () => setConfirming(false),
              )
            }
          >
            Yes
          </Button>
          <Button small disabled={write.busy} onClick={() => setConfirming(false)}>
            No
          </Button>
        </span>
      ) : (
        <Button
          variant="quiet"
          tone="negative"
          small
          data-ui="ui.parties.deleteAccount"
          onClick={() => setConfirming(true)}
        >
          Delete
        </Button>
      )}
      {write.problem !== null && <Note tone="negative">{write.problem}</Note>}
      {write.unsure && <Button onClick={() => onDone('Refreshed')}>Refresh</Button>}
    </li>
  );
}

export function CustomerModal({
  customer,
  onClose,
  onDone,
}: {
  customer: PartyCustomer;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const detail = useResource(`customer:${customer.id}`, (signal) =>
    loadCustomerDetail(customer.id, signal),
  );
  const write = useWrite(onDone);
  const [editing, setEditing] = useState<CustomerDetail | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [addingAccount, setAddingAccount] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const accountsRef = useRef<HTMLDivElement>(null);
  const accounts = customer.accounts;
  const before = detail.status === 'ok' ? detail.data : null;
  const changes = before && editing ? customerChanges(before, editing) : null;
  const set = (change: Partial<CustomerDetail>) =>
    setEditing((e) => (e === null ? e : { ...e, ...change }));

  const body =
    before === null ? null : editing !== null ? (
      <section className="bss-form">
        <Field label="Customer name">
          <TextInput value={editing.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <Segmented
          label="Customer status"
          value={editing.status as CustomerStatus}
          onChange={(status) => set({ status })}
          options={CUSTOMER_STATUSES.map((key) => ({ key, label: key }))}
        />
      </section>
    ) : (
      <Facts
        rows={[
          ['Name', before.name],
          ['Status', <StatusPill kind="customer" key="s" status={before.status} />],
          ['Engaged party', `${before.partyName || shortId(before.partyId)} · ${before.partyKind}`],
          [
            'Billing accounts',
            <div key="a" ref={accountsRef}>
              <CustomerAccounts links={accounts} onDone={onDone} />
            </div>,
          ],
          [
            'Security',
            <Button
              key="audit"
              small
              data-ui="ui.parties.customerAudit"
              aria-expanded={showAudit}
              onClick={() => setShowAudit((shown) => !shown)}
            >
              {showAudit ? 'Hide audit log' : 'Audit log'}
            </Button>,
          ],
        ]}
      />
    );
  // the read-only audit log loads only when asked for (no request on opening the dialog)
  const shownBody =
    body !== null && editing === null && showAudit ? (
      <>
        {body}
        <AuditLog customerId={customer.id} />
      </>
    ) : (
      body
    );

  return (
    <>
      {addingAccount && (
        <AddBillingAccountModal
          customer={customer}
          onClose={() => setAddingAccount(false)}
          onCreated={() => onDone('Billing account created')}
        />
      )}
      <Frame
        title={customer.name}
        ui="ui.parties.customer"
        subtitle={`customer · ${shortId(customer.id)}`}
        state={detail.status}
        {...(detail.status === 'error' ? { error: detail.error } : {})}
        retry={detail.retry}
        body={shownBody}
        write={write}
        onClose={onClose}
        onRefresh={onDone.bind(null, 'Refreshed')}
        actions={
          editing === null ? (
            <>
              {accounts.length > 0 ? (
                <Button
                  variant="quiet"
                  className="footer-push"
                  data-ui="ui.parties.removeAccountsFirst"
                  onClick={() => accountsRef.current?.scrollIntoView?.({ block: 'center' })}
                >
                  Remove billing accounts first
                </Button>
              ) : (
                <DeleteControl
                  noun="customer"
                  block={null}
                  confirming={confirming}
                  setConfirming={setConfirming}
                  busy={write.busy}
                  onDelete={() =>
                    void write.run(
                      async () => {
                        await deleteCustomer(customer.id);
                        return `${customer.name} deleted`;
                      },
                      () => setConfirming(false),
                    )
                  }
                />
              )}
              <Button
                data-ui="ui.parties.addAccount"
                disabled={before === null}
                onClick={() => setAddingAccount(true)}
              >
                Add billing account
              </Button>
              <Button onClick={onClose}>Close</Button>
              <Button
                variant="primary"
                data-ui="ui.parties.edit"
                disabled={before === null}
                onClick={() => before && setEditing(before)}
              >
                Edit
              </Button>
            </>
          ) : (
            <>
              <Button
                onClick={() => {
                  setEditing(null);
                  write.clear();
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={editing.name.trim() === '' || changes === null || write.busy}
                onClick={() =>
                  changes &&
                  void write.run(async () => {
                    await patchCustomer(customer.id, changes);
                    return `${customer.name} saved`;
                  })
                }
              >
                Save
              </Button>
            </>
          )
        }
      />
    </>
  );
}
