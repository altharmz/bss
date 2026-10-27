import { Note } from '../ui/Note';
import { useState, type ReactNode } from 'react';
import type { CustomerStatus } from '../api/bss';
import { explain } from '../api/client';
import {
  accountsOf,
  customerBlock,
  customerChanges,
  customersOf,
  deleteCustomer,
  deleteParty,
  loadCustomerDetail,
  loadPartyDetail,
  loadParties,
  partyBlock,
  partyChanges,
  patchCustomer,
  patchParty,
  type CustomerDetail,
  type Parties,
  type Party,
  type PartyCustomer,
  type PartyDetail,
} from '../api/parties';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { shortId } from '../format/ids';
import { formatInt } from '../format/money';
import {
  Button,
  Modal,
  PageTitle,
  Panel,
  StatusPill,
  Segmented,
  TextInput,
  useToast,
} from '../ui/kit';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { CUSTOMER_STATUSES, Field } from './SubscriberForms';
import { uncertain } from './submissionKey';
import './screens.css';

// Individuals, organizations and customers, whether or not they have a billing account.
// A row opens the record (GET) with its subscriber links, a names-and-status edit that
// reuses the subscriber form fields, and Delete. Delete is offered unless the loaded data
// already shows a dependency; the server's 409 still shows. Design:
// docs/next-slices-design.md section 15.

type Tab = 'individuals' | 'organizations' | 'customers';

const PARTY_COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'status', label: 'Status', width: '130px' },
  { key: 'customers', label: 'Customers', width: '110px', align: 'right', mono: true },
  { key: 'subscribers', label: 'Subscribers', width: '1fr' },
];
const CUSTOMER_COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'status', label: 'Status', width: '110px' },
  { key: 'party', label: 'Engaged party', width: '1fr' },
  { key: 'accounts', label: 'Billing accounts', width: '1.4fr' },
];

const dash = <span className="bss-dash">—</span>;

type Open = { party: Party } | { customer: PartyCustomer };

function AccountLinks({ links }: { links: { id: string; name: string }[] }) {
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
  const [status, setStatus] = useState('All');
  const [open, setOpen] = useState<Open | null>(null);
  const resource = useResource(`parties:${version}`, loadParties);
  const q = query.trim().toLowerCase();
  const data = resource.status === 'ok' ? resource.data : null;

  const customers = tab === 'customers';
  const parties =
    data === null ? [] : tab === 'individuals' ? data.individuals : data.organizations;
  const statusOf = (): string[] =>
    customers ? [] : [...new Set(parties.map((p) => p.status).filter(Boolean))].sort();
  const statuses = customers ? ['Created', 'Active', 'Inactive'] : statusOf();
  const countOf = (s: string) =>
    (customers ? (data?.customers ?? []) : parties).filter((r) => s === 'All' || r.status === s)
      .length;
  const changeTab = (next: Tab) => {
    setTab(next);
    setStatus('All');
  };
  const reset = () => {
    setQuery('');
    setStatus('All');
  };

  return (
    <div className="bss-screen" data-ui="ui.parties">
      <div className="bss-title-row">
        <PageTitle
          title="Parties"
          count={
            data
              ? formatInt(
                  customers
                    ? data.customers.length
                    : tab === 'individuals'
                      ? data.individuals.length
                      : data.organizations.length,
                )
              : undefined
          }
        />
      </div>
      <div className="bss-toolbar">
        <Segmented
          label="Party sections"
          value={tab}
          onChange={changeTab}
          options={[
            { key: 'individuals', label: 'Individuals' },
            { key: 'organizations', label: 'Organizations' },
            { key: 'customers', label: 'Customers' },
          ]}
        />
      </div>
      <div className="bss-toolbar">
        <TextInput
          width={300}
          placeholder={`Search ${tab}`}
          aria-label={`Search ${tab}`}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {data !== null && statuses.length > 0 && (
          <Segmented
            label="Status"
            value={status}
            onChange={setStatus}
            options={['All', ...statuses].map((key) => ({
              key,
              label: key,
              count: countOf(key),
            }))}
          />
        )}
      </div>
      <Panel>
        <div data-ui={`ui.parties.${tab}`}>
          {customers ? (
            <CustomersList
              resource={resource}
              q={q}
              status={status}
              reset={reset}
              onOpen={(customer) => setOpen({ customer })}
            />
          ) : (
            <PartiesList
              resource={resource}
              tab={tab}
              q={q}
              status={status}
              reset={reset}
              onOpen={(party) => setOpen({ party })}
            />
          )}
        </div>
      </Panel>
      {open !== null && data !== null && (
        <RecordModal
          open={open}
          data={data}
          onClose={() => setOpen(null)}
          onDone={(message) => {
            setOpen(null);
            toast(message);
            setVersion((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}

type PartiesResource = ReturnType<typeof useResource<Parties>>;

function PartiesList({
  resource,
  tab,
  q,
  status,
  reset,
  onOpen,
}: {
  resource: PartiesResource;
  tab: Tab;
  q: string;
  status: string;
  reset: () => void;
  onOpen: (party: Party) => void;
}) {
  const all = (d: Parties) => (tab === 'individuals' ? d.individuals : d.organizations);
  return (
    <>
      <TableHead columns={PARTY_COLUMNS} />
      <ListState
        resource={resource}
        columns={PARTY_COLUMNS}
        errorTitle="Failed to load parties"
        empty={{ title: `No ${tab} yet`, text: 'Subscribers create their party.' }}
        total={(d) => all(d).length}
        rows={(d) =>
          all(d).filter(
            (p) =>
              (status === 'All' || p.status === status) && (!q || p.name.toLowerCase().includes(q)),
          )
        }
        onResetFilters={reset}
      >
        {(rows, d) => (
          <>
            {rows.map((party) => {
              const own = customersOf(d, party.id);
              const accounts = accountsOf(
                d,
                own.map((c) => c.id),
              );
              return (
                <TableRow
                  key={party.id}
                  columns={PARTY_COLUMNS}
                  onClick={() => onOpen(party)}
                  cells={{
                    name: <span className="bss-name ellipsis">{party.name}</span>,
                    status: party.status ? <StatusPill kind="party" status={party.status} /> : dash,
                    customers: <span className={own.length ? '' : 'bss-zero'}>{own.length}</span>,
                    subscribers: <AccountLinks links={accounts} />,
                  }}
                />
              );
            })}
            <TableFooter
              left={`Showing ${formatInt(rows.length)} of ${formatInt(all(d).length)}`}
              right="A party without customers can be deleted"
            />
          </>
        )}
      </ListState>
    </>
  );
}

function CustomersList({
  resource,
  q,
  status,
  reset,
  onOpen,
}: {
  resource: PartiesResource;
  q: string;
  status: string;
  reset: () => void;
  onOpen: (customer: PartyCustomer) => void;
}) {
  return (
    <>
      <TableHead columns={CUSTOMER_COLUMNS} />
      <ListState
        resource={resource}
        columns={CUSTOMER_COLUMNS}
        errorTitle="Failed to load customers"
        empty={{ title: 'No customers yet', text: 'Subscribers create their customer.' }}
        total={(d) => d.customers.length}
        rows={(d) =>
          d.customers.filter(
            (c) =>
              (status === 'All' || c.status === status) && (!q || c.name.toLowerCase().includes(q)),
          )
        }
        onResetFilters={reset}
      >
        {(rows, d) => (
          <>
            {rows.map((customer) => {
              const party = [...d.individuals, ...d.organizations].find(
                (p) => p.id === customer.partyId,
              );
              return (
                <TableRow
                  key={customer.id}
                  columns={CUSTOMER_COLUMNS}
                  onClick={() => onOpen(customer)}
                  cells={{
                    name: <span className="bss-name ellipsis">{customer.name}</span>,
                    status: <StatusPill kind="customer" status={customer.status} />,
                    party: (
                      <div className="ellipsis">
                        {party?.name ?? shortId(customer.partyId)}
                        <span className="bss-secondary"> · {customer.partyKind}</span>
                      </div>
                    ),
                    accounts: <AccountLinks links={accountsOf(d, [customer.id])} />,
                  }}
                />
              );
            })}
            <TableFooter
              left={`Showing ${formatInt(rows.length)} of ${formatInt(d.customers.length)}`}
              right="A customer without billing accounts can be deleted"
            />
          </>
        )}
      </ListState>
    </>
  );
}

function RecordModal({
  open,
  data,
  onClose,
  onDone,
}: {
  open: Open;
  data: Parties;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  if ('party' in open)
    return <PartyModal party={open.party} data={data} onClose={onClose} onDone={onDone} />;
  return <CustomerModal customer={open.customer} data={data} onClose={onClose} onDone={onDone} />;
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="bss-facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
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
    <Modal
      title={p.title}
      width={560}
      onClose={p.onClose}
      ui={p.ui}
      subtitle={p.subtitle}
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
      <div className="bss-modal-body">
        {p.state === 'loading' && (
          <SkeletonRows columns={[{ key: 'a', label: '', width: '1fr' }]} rows={4} />
        )}
        {p.state === 'error' && p.error && (
          <ErrorBlock title="Failed to load the record" error={p.error} onRetry={p.retry} />
        )}
        {p.state === 'ok' && p.body}
      </div>
    </Modal>
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

function PartyModal({
  party,
  data,
  onClose,
  onDone,
}: {
  party: Party;
  data: Parties;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const detail = useResource(`party:${party.kind}:${party.id}`, (signal) =>
    loadPartyDetail(party.kind, party.id, signal),
  );
  const write = useWrite(onDone);
  const [editing, setEditing] = useState<PartyDetail | null>(null);
  const [confirming, setConfirming] = useState(false);
  const own = customersOf(data, party.id);
  const accounts = accountsOf(
    data,
    own.map((c) => c.id),
  );
  const before = detail.status === 'ok' ? detail.data : null;
  const changes = before && editing ? partyChanges(before, editing) : null;
  const named =
    editing !== null &&
    (editing.kind === 'Individual'
      ? editing.givenName.trim() !== '' && editing.familyName.trim() !== ''
      : editing.organizationName.trim() !== '');
  const set = (change: Partial<PartyDetail>) =>
    setEditing((e) => (e === null ? e : { ...e, ...change }));
  const label = party.kind === 'Individual' ? 'person' : 'organization';

  const body =
    before === null ? null : editing !== null ? (
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
          </>
        ) : (
          <Field label="Organization name">
            <TextInput
              value={editing.organizationName}
              onChange={(e) => set({ organizationName: e.target.value })}
            />
          </Field>
        )}
      </section>
    ) : (
      <>
        <Facts
          rows={[
            ['Type', party.kind === 'Individual' ? 'Person' : 'Organization'],
            ...(before.kind === 'Individual'
              ? ([
                  ['Given name', before.givenName],
                  ['Family name', before.familyName],
                ] as [string, ReactNode][])
              : ([['Organization name', before.organizationName]] as [string, ReactNode][])),
            ['Status', before.status ? <StatusPill kind="party" status={before.status} /> : dash],
            ['Customers', own.length ? own.map((c) => c.name).join(', ') : dash],
            ['Subscribers', <AccountLinks key="s" links={accounts} />],
          ]}
        />
      </>
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
              block={partyBlock(data, party)}
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
              disabled={!named || changes === null || write.busy}
              onClick={() =>
                changes &&
                void write.run(async () => {
                  await patchParty(party.kind, party.id, changes);
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

function CustomerModal({
  customer,
  data,
  onClose,
  onDone,
}: {
  customer: PartyCustomer;
  data: Parties;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const detail = useResource(`customer:${customer.id}`, (signal) =>
    loadCustomerDetail(customer.id, signal),
  );
  const write = useWrite(onDone);
  const [editing, setEditing] = useState<CustomerDetail | null>(null);
  const [confirming, setConfirming] = useState(false);
  const accounts = accountsOf(data, [customer.id]);
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
          ['Billing accounts', <AccountLinks key="a" links={accounts} />],
        ]}
      />
    );

  return (
    <Frame
      title={customer.name}
      ui="ui.parties.customer"
      subtitle={`customer · ${shortId(customer.id)}`}
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
              noun="customer"
              block={customerBlock(data, customer)}
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
  );
}
