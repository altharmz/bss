import { useState } from 'react';
import { loadSubscribers, type Account, type AccountState } from '../api/bss';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { hrefOf, useRouter } from '../app/router';
import { matchesShortId, shortId } from '../format/ids';
import { formatInt } from '../format/money';
import { Amount } from '../ui/Amount';
import { PageTitle, Panel, Pill, Segmented, TextInput } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// Screen 1. A row is a billing account (D1): a customer with two accounts is two rows.
// Status is the account's real state. Balance comes with the account list; active
// products from one product list (status=active), grouped - no request per row (D4).

export const COLUMNS: Column[] = [
  { key: 'no', label: 'Subscriber no.', width: '150px', mono: true },
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'status', label: 'Status', width: '120px' },
  { key: 'balance', label: 'Balance', width: '190px', align: 'right', mono: true },
  { key: 'products', label: 'Active products', width: '160px', align: 'right', mono: true },
  { key: 'cycle', label: 'Cycle day', width: '150px', align: 'right', mono: true },
];

type StateFilter = 'All' | AccountState;
const STATES: StateFilter[] = ['All', 'Active', 'Suspended', 'Closed'];

function matches(account: Account, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    account.customerName.toLowerCase().includes(q) ||
    account.name.toLowerCase().includes(q) ||
    matchesShortId(q, account.customerId) ||
    matchesShortId(q, account.id)
  );
}

export function SubscribersScreen() {
  useSetCrumbs([{ label: 'Subscribers' }], 'subscribers');
  const { navigate } = useRouter();
  const [query, setQuery] = useState('');
  const [state, setState] = useState<StateFilter>('All');
  const subscribers = useResource('subscribers', loadSubscribers);

  const accounts = subscribers.status === 'ok' ? subscribers.data.accounts : [];
  const countOf = (filter: StateFilter) =>
    filter === 'All' ? accounts.length : accounts.filter((a) => a.state === filter).length;
  const negative = accounts.filter((a) => a.balanceCents < 0).length;
  const reset = () => {
    setQuery('');
    setState('All');
  };

  return (
    <div className="bss-screen" data-ui="ui.subscribers.list">
      <PageTitle
        title="Subscribers"
        count={subscribers.status === 'ok' ? formatInt(accounts.length) : undefined}
      />
      <div className="bss-toolbar">
        <TextInput
          width={340}
          placeholder="Search by name or subscriber number"
          aria-label="Search subscribers"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Segmented
          label="Account state"
          value={state}
          onChange={setState}
          options={STATES.map((key) => ({
            key,
            label: key,
            ...(subscribers.status === 'ok' ? { count: countOf(key) } : {}),
          }))}
        />
        <span className="right">
          Negative balance: <span className="neg-count">{formatInt(negative)}</span>
        </span>
      </div>
      <Panel>
        <TableHead columns={COLUMNS} />
        <ListState
          resource={subscribers}
          columns={COLUMNS}
          errorTitle="Failed to load subscribers"
          empty={{
            title: 'No subscribers yet',
            text: 'Subscribers appear after registration via CRM or import.',
          }}
          total={(data) => data.accounts.length}
          rows={(data) =>
            data.accounts.filter((a) => (state === 'All' || a.state === state) && matches(a, query))
          }
          onResetFilters={reset}
        >
          {(rows, data) => {
            const perCustomer = new Map<string, number>();
            for (const a of data.accounts) {
              perCustomer.set(a.customerId, (perCustomer.get(a.customerId) ?? 0) + 1);
            }
            return (
              <>
                {rows.map((account) => {
                  const active = data.activeProducts.get(account.id) ?? 0;
                  return (
                    <TableRow
                      key={account.id}
                      columns={COLUMNS}
                      onClick={() =>
                        navigate(hrefOf({ name: 'subscriber', accountId: account.id }))
                      }
                      cells={{
                        no: (
                          <span className="bss-subno" title={account.customerId}>
                            {shortId(account.customerId)}
                          </span>
                        ),
                        name: (
                          <div className="ellipsis">
                            <span className="bss-name">{account.customerName}</span>
                            {(perCustomer.get(account.customerId) ?? 0) > 1 && (
                              <span className="bss-secondary"> · {account.name}</span>
                            )}
                          </div>
                        ),
                        status: <Pill status={account.state} />,
                        balance: (
                          <Amount cents={account.balanceCents} currency={account.currency} chip />
                        ),
                        products: <span className={active ? '' : 'bss-zero'}>{active}</span>,
                        cycle: `Day ${account.cycleDay}`,
                      }}
                    />
                  );
                })}
                <TableFooter
                  left={`Showing ${formatInt(rows.length)} of ${formatInt(data.accounts.length)}`}
                  right="Amounts in billing account currency"
                />
              </>
            );
          }}
        </ListState>
      </Panel>
    </div>
  );
}
