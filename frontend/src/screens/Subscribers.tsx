import { useInspectorFit } from '../ui/inspectorFit';
import { useEffect, useRef, useState } from 'react';
import {
  searchSubscribers,
  SUBSCRIBER_PAGE,
  type Account,
  type AccountState,
  type RatingMode,
} from '../api/bss';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { hrefOf, useRouter } from '../app/router';
import { Amount } from '../ui/Amount';
import { Id } from '../ui/Id';
import {
  Button,
  Flag,
  KeyValue,
  PageHeader,
  StatusPill,
  Segmented,
  Tag,
  TextInput,
  useToast,
  Icon,
} from '../ui/kit';
import { useMediaQuery, WIDE_QUERY } from '../ui/useMediaQuery';
import { ListState } from '../ui/states';
import { formatInt } from '../format/money';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { PageFooter, usePageOffset, useSettled } from '../ui/paging';
import { NewSubscriberModal } from './SubscriberForms';
import './screens.css';

// Screen 1. A row is a billing account (D1): a customer with two accounts is two rows.
// Status is the account's real state. Paged and searched on the server (#314): a prefix
// of a customer or account name, a subscriber number or a short id; the server computes
// each row's balance, active products and customer account count for the page. No
// counts (#312): the filters and the header show none.

export const COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: 'minmax(0, 1.6fr)' },
  { key: 'account', label: 'Account', width: '140px' },
  { key: 'status', label: 'Status', width: '120px' },
  { key: 'mode', label: 'Mode', width: '96px' },
  { key: 'products', label: 'Products', width: '80px', align: 'right', mono: true },
  { key: 'cycle', label: 'Cycle day', width: '88px', align: 'right', mono: true },
  { key: 'balance', label: 'Balance', width: '180px', align: 'right' },
];

type StateFilter = 'All' | AccountState;
const STATES: StateFilter[] = ['All', 'Active', 'Suspended', 'Closed'];
type ModeFilter = 'All' | RatingMode;
const MODES: { key: ModeFilter; label: string }[] = [
  { key: 'All', label: 'All' },
  { key: 'postpaid', label: 'Postpaid' },
  { key: 'prepaid', label: 'Prepaid' },
];

function SubscriberInspector({
  account,
  active,
  onOpen,
}: {
  account: Account;
  active: number;
  onOpen: () => void;
}) {
  const [fitRef, fitStyle] = useInspectorFit<HTMLElement>();
  const overLimit =
    account.creditLimitCents !== null && account.balanceCents < -account.creditLimitCents;
  return (
    <aside
      ref={fitRef}
      style={fitStyle}
      className="bss-inspector bss-subscriber-inspector"
      data-ui="ui.subscribers.inspector"
      aria-label={`${account.customerName} account`}
    >
      <div className="bss-modal">
        <div className="bss-dialog-body">
          <h2 className="bss-inspector-name">{account.customerName}</h2>
          <div className="bss-inspector-meta">
            <Id prefix="BA-" id={account.id} />
            <StatusPill kind="account" status={account.state} />
            <Tag>{account.ratingType === 'prepaid' ? 'Prepaid' : 'Postpaid'}</Tag>
          </div>
          <div className="bss-inspector-balance">
            <span className="bss-inspector-label">Balance</span>
            <span className="bss-inspector-figure">
              <Amount cents={account.balanceCents} currency={account.currency} />
            </span>
            {overLimit && <Flag>Over limit</Flag>}
          </div>
          <KeyValue
            items={[
              ['Customer', account.customerName],
              ['Cycle day', account.ratingType === 'prepaid' ? '—' : account.cycleDay],
              [
                'Credit limit',
                account.creditLimitCents === null ? (
                  '—'
                ) : (
                  <Amount cents={account.creditLimitCents} currency={account.currency} />
                ),
              ],
              ['Products', `${formatInt(active)} active`],
            ]}
          />
        </div>
        <footer className="bss-modal-footer">
          <Button variant="primary" onClick={onOpen}>
            Open account
          </Button>
        </footer>
      </div>
    </aside>
  );
}

export function SubscribersScreen() {
  useSetCrumbs([{ label: 'Subscribers' }], 'subscribers');
  const { navigate } = useRouter();
  const [query, setQuery] = useState('');
  const q = useSettled(query);
  const [state, setState] = useState<StateFilter>('All');
  const [mode, setMode] = useState<ModeFilter>('All');
  // prepaid accounts with live lines and no service address (#207)
  const [noAddress, setNoAddress] = useState(false);
  // the offset belongs to the filters it was set for: new filters start at the first page
  const filterKey = `${q}:${state}:${mode}:${noAddress}`;
  const [offset, setOffset] = usePageOffset(filterKey);
  const subscribers = useResource(`subscribers:${filterKey}:${offset}`, (signal) =>
    searchSubscribers(
      {
        q,
        state: state === 'All' ? null : state,
        mode: mode === 'All' ? null : mode,
        noServiceAddress: noAddress,
      },
      offset,
      signal,
    ),
  );
  const [creating, setCreating] = useState(false);
  const wide = useMediaQuery(WIDE_QUERY);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const toast = useToast();
  const search = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
      if (
        event.target instanceof Element &&
        event.target.closest('input, textarea, select, [contenteditable="true"]')
      )
        return;
      event.preventDefault();
      search.current?.querySelector('input')?.focus();
    };
    window.addEventListener('keydown', focusSearch);
    return () => window.removeEventListener('keydown', focusSearch);
  }, []);

  useEffect(() => {
    if (!wide || selectedId === null) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelectedId(null);
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [wide, selectedId]);

  const rows = subscribers.status === 'ok' ? subscribers.data.items : [];
  const currencies = new Set(rows.map((account) => account.currency));
  const columns = COLUMNS.map((column) =>
    column.key === 'balance' && currencies.size === 1
      ? { ...column, label: `Balance ${rows[0]?.currency}` }
      : column,
  );
  const filtered = query !== '' || state !== 'All' || mode !== 'All' || noAddress;
  const reset = () => {
    setQuery('');
    setState('All');
    setMode('All');
    setNoAddress(false);
  };

  const openCard = (accountId: string) => navigate(hrefOf({ name: 'subscriber', accountId }));
  const selected = wide ? (rows.find((a) => a.id === selectedId) ?? null) : null;

  return (
    <div className="bss-screen bss-subscribers-screen" data-ui="ui.subscribers.list">
      {creating && (
        <NewSubscriberModal
          onClose={() => setCreating(false)}
          onCreated={(accountId, name) => {
            setCreating(false);
            toast(`${name} created`);
            navigate(hrefOf({ name: 'subscriber', accountId }));
          }}
        />
      )}
      <PageHeader
        title="Subscribers"
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            New subscriber
          </Button>
        }
        filters={
          <>
            <div className="bss-subscriber-search" ref={search}>
              <Icon name="search" size={14} />
              <TextInput
                placeholder="Name, BA id or subscriber no. (starts with)"
                aria-label="Search subscribers"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <kbd aria-hidden="true">/</kbd>
            </div>
            <Segmented
              label="Account state"
              value={state}
              onChange={setState}
              options={STATES.map((key) => ({ key, label: key }))}
            />
            <Segmented
              label="Account mode"
              value={mode}
              onChange={setMode}
              options={MODES.map(({ key, label }) => ({ key, label }))}
            />
            <Button
              data-ui="ui.subscribers.noServiceAddress"
              aria-pressed={noAddress}
              title="Prepaid accounts with live lines and no service address: new lines are refused and charges are taxed at the flat rate"
              onClick={() => setNoAddress(!noAddress)}
            >
              No service address
            </Button>
            {filtered && (
              <Button variant="quiet" onClick={reset}>
                Reset filters
              </Button>
            )}
          </>
        }
      />
      <div className="bss-with-inspector bss-subscribers-body">
        <div role="table" aria-label="Subscribers" className="bss-subscribers-table">
          <TableHead columns={columns} />
          <ListState
            resource={subscribers}
            columns={columns}
            errorTitle="Failed to load subscribers"
            empty={{ title: 'No subscribers yet', text: 'Create one with New subscriber.' }}
            // filtered on the server: an empty filtered page is "no matches", not an empty system
            count={(data) => (filtered ? 1 : data.items.length)}
            rows={(data) => data.items}
            query={query}
            filters={[
              ...(state !== 'All' ? [{ label: 'Status', value: state }] : []),
              ...(mode !== 'All'
                ? [{ label: 'Mode', value: mode === 'prepaid' ? 'Prepaid' : 'Postpaid' }]
                : []),
              ...(noAddress ? [{ label: 'Service address', value: 'Missing' }] : []),
            ]}
            onResetFilters={reset}
          >
            {(page, data) => (
              <>
                {page.map((account) => (
                  <TableRow
                    key={account.id}
                    columns={columns}
                    onClick={() => (wide ? setSelectedId(account.id) : openCard(account.id))}
                    {...(wide ? { onActivate: () => openCard(account.id) } : {})}
                    selected={wide && account.id === selectedId}
                    cells={{
                      name: (
                        <div className="ellipsis">
                          <span className="bss-name">{account.customerName}</span>
                          {account.customerAccounts > 1 && (
                            <span className="bss-secondary"> · {account.name}</span>
                          )}
                        </div>
                      ),
                      account: <Id prefix="BA-" id={account.id} />,
                      status: <StatusPill kind="account" status={account.state} />,
                      mode: (
                        <span
                          className="bss-subscriber-mode"
                          data-ui={
                            account.ratingType === 'prepaid' ? 'ui.subscribers.prepaid' : undefined
                          }
                        >
                          {account.ratingType === 'prepaid' ? 'Prepaid' : 'Postpaid'}
                        </span>
                      ),
                      balance: (
                        <span className="bss-subscriber-balance">
                          {account.creditLimitCents !== null &&
                            account.balanceCents < -account.creditLimitCents && (
                              <Flag>Over limit</Flag>
                            )}
                          <Amount cents={account.balanceCents} currency={account.currency} />
                        </span>
                      ),
                      products: (
                        <span className={account.activeProducts ? '' : 'bss-zero'}>
                          {account.activeProducts}
                        </span>
                      ),
                      cycle: account.ratingType === 'prepaid' ? '—' : account.cycleDay,
                    }}
                  />
                ))}
                <PageFooter
                  offset={data.offset}
                  shown={page.length}
                  more={data.more}
                  size={SUBSCRIBER_PAGE}
                  onOffset={setOffset}
                  ui="ui.subscribers"
                  note="amounts in billing account currency"
                />
              </>
            )}
          </ListState>
        </div>
        {selected && (
          <SubscriberInspector
            account={selected}
            active={selected.activeProducts}
            onOpen={() => openCard(selected.id)}
          />
        )}
      </div>
    </div>
  );
}
