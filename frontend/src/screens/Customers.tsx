import { useState } from 'react';
import { loadCustomerPage, PARTY_PAGE, type PartyCustomer } from '../api/parties';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { shortId } from '../format/ids';
import { PageHeader, Segmented, StatusPill, TextInput, useToast } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { PageFooter, usePageOffset, useSettled } from '../ui/paging';
import { AccountLinks, CustomerModal, type Paging } from './Parties';
import { CUSTOMER_STATUSES } from './SubscriberForms';
import './screens.css';

// TMF629 customers, each with its engaged party and its billing accounts (#457): its own
// screen in the Customers section, between Financial accounts and Billing accounts. It was
// a tab of Parties; the list, its filters and the customer dialog are the same, and so are
// the data-ui ids (ui.parties.customers, ui.parties.customer, ...). A customer is created
// from its party (Parties, New customer). Design: docs/ui.md.

const COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'status', label: 'Status', width: '110px' },
  { key: 'party', label: 'Engaged party', width: '1fr' },
  { key: 'accounts', label: 'Billing accounts', width: '1.4fr' },
];

export function CustomersScreen() {
  useSetCrumbs([{ label: 'Customers' }], 'customers');
  const toast = useToast();
  const [version, setVersion] = useState(0);
  const [query, setQuery] = useState('');
  const q = useSettled(query);
  const [status, setStatus] = useState('All');
  const [open, setOpen] = useState<PartyCustomer | null>(null);

  const filter = { q, status: status === 'All' ? null : status };
  const filterKey = `customers:${q}:${status}:${version}`;
  const [offset, setOffset] = usePageOffset(filterKey);
  const filtered = query !== '' || status !== 'All';
  const reset = () => {
    setQuery('');
    setStatus('All');
  };
  const done = (message: string) => {
    setOpen(null);
    toast(message);
    setVersion((v) => v + 1);
  };

  return (
    <div className="bss-screen" data-ui="ui.customers">
      <PageHeader
        title="Customers"
        filters={
          <>
            <TextInput
              width={300}
              placeholder="Name or id (starts with)"
              aria-label="Search customers"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Segmented
              label="Status"
              value={status}
              onChange={setStatus}
              options={['All', ...CUSTOMER_STATUSES].map((key) => ({ key, label: key }))}
            />
          </>
        }
      />
      <div role="table" aria-label="Customers" className="bss-page-table">
        <div data-ui="ui.parties.customers">
          <CustomersList
            filterKey={filterKey}
            offset={offset}
            setOffset={setOffset}
            filter={filter}
            filtered={filtered}
            reset={reset}
            onOpen={setOpen}
          />
        </div>
      </div>
      {open !== null && (
        <CustomerModal customer={open} onClose={() => setOpen(null)} onDone={done} />
      )}
    </div>
  );
}

function CustomersList({
  filterKey,
  offset,
  setOffset,
  filter,
  filtered,
  reset,
  onOpen,
}: Paging & { onOpen: (customer: PartyCustomer) => void }) {
  const resource = useResource(`party-customers:${filterKey}:${offset}`, (signal) =>
    loadCustomerPage(filter, offset, signal),
  );
  return (
    <>
      <TableHead columns={COLUMNS} />
      <ListState
        resource={resource}
        columns={COLUMNS}
        errorTitle="Failed to load customers"
        empty={{
          title: 'No customers yet',
          text: 'A new billing account creates its customer; Parties adds one to a party.',
        }}
        // filtered on the server: an empty filtered page is "no matches"
        count={(d) => (filtered ? 1 : d.items.length)}
        rows={(d) => d.items}
        query={filter.q || undefined}
        onResetFilters={reset}
      >
        {(rows, d) => (
          <>
            {rows.map((customer) => (
              <TableRow
                key={customer.id}
                columns={COLUMNS}
                onClick={() => onOpen(customer)}
                cells={{
                  name: <span className="bss-name ellipsis">{customer.name}</span>,
                  status: <StatusPill kind="customer" status={customer.status} />,
                  party: (
                    <div className="ellipsis">
                      {customer.partyName || shortId(customer.partyId)}
                      <span className="bss-secondary"> · {customer.partyKind}</span>
                    </div>
                  ),
                  accounts: <AccountLinks links={customer.accounts} />,
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
              note="a customer without billing accounts can be deleted"
            />
          </>
        )}
      </ListState>
    </>
  );
}
