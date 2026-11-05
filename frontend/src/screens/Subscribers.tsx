import { useInspectorFit } from '../ui/inspectorFit';
import { useEffect, useRef, useState } from 'react';
import { loadSubscribers, type Account, type AccountState, type RatingMode } from '../api/bss';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { hrefOf, useRouter } from '../app/router';
import { matchesShortId } from '../format/ids';
import { formatInt } from '../format/money';
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
} from '../ui/kit';
import { useMediaQuery, WIDE_QUERY } from '../ui/useMediaQuery';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { NewSubscriberModal } from './SubscriberForms';
import './screens.css';

// Screen 1. A row is a billing account (D1): a customer with two accounts is two rows.
// Status is the account's real state. Balance comes with the account list; active
// products from one product list (status=active), grouped - no request per row (D4).

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
  const [state, setState] = useState<StateFilter>('All');
  const [mode, setMode] = useState<ModeFilter>('All');
  const subscribers = useResource('subscribers', loadSubscribers);
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

  const accounts = subscribers.status === 'ok' ? subscribers.data.accounts : [];
  const currencies = new Set(accounts.map((account) => account.currency));
  const columns = COLUMNS.map((column) =>
    column.key === 'balance' && currencies.size === 1
      ? { ...column, label: `Balance ${accounts[0]?.currency}` }
      : column,
  );
  const countOf = (filter: StateFilter) =>
    filter === 'All' ? accounts.length : accounts.filter((a) => a.state === filter).length;
  const modeCount = (filter: ModeFilter) =>
    filter === 'All' ? accounts.length : accounts.filter((a) => a.ratingType === filter).length;
  const visibleAccounts = accounts.filter(
    (a) =>
      (state === 'All' || a.state === state) &&
      (mode === 'All' || a.ratingType === mode) &&
      matches(a, query),
  );
  const filtered = query !== '' || state !== 'All' || mode !== 'All';
  const negative = accounts.filter((a) => a.balanceCents < 0).length;
  const reset = () => {
    setQuery('');
    setState('All');
    setMode('All');
  };

  const openCard = (accountId: string) => navigate(hrefOf({ name: 'subscriber', accountId }));
  const selected =
    wide && subscribers.status === 'ok'
      ? (accounts.find((a) => a.id === selectedId) ?? null)
      : null;

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
        count={subscribers.status === 'ok' ? formatInt(visibleAccounts.length) : undefined}
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            New subscriber
          </Button>
        }
        filters={
          <>
            <div className="bss-subscriber-search" ref={search}>
              <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
                <circle cx="6" cy="6" r="4.5" />
                <path d="M9.5 9.5L13 13" />
              </svg>
              <TextInput
                placeholder="Name, BA id or subscriber no."
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
              options={STATES.map((key) => ({
                key,
                label: key,
                ...(subscribers.status === 'ok' ? { count: countOf(key) } : {}),
              }))}
            />
            <Segmented
              label="Account mode"
              value={mode}
              onChange={setMode}
              options={MODES.map(({ key, label }) => ({
                key,
                label,
                ...(subscribers.status === 'ok' ? { count: modeCount(key) } : {}),
              }))}
            />
            {filtered && (
              <Button variant="quiet" onClick={reset}>
                Reset filters
              </Button>
            )}
            <span className="bss-subscriber-negative">
              Negative balance <span className="neg-count">{formatInt(negative)}</span>
            </span>
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
            empty={{
              title: 'No subscribers yet',
              text: 'Create one with New subscriber.',
            }}
            total={(data) => data.accounts.length}
            rows={() => visibleAccounts}
            query={query}
            filters={[
              ...(state !== 'All' ? [{ label: 'Status', value: state }] : []),
              ...(mode !== 'All'
                ? [{ label: 'Mode', value: mode === 'prepaid' ? 'Prepaid' : 'Postpaid' }]
                : []),
            ]}
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
                        columns={columns}
                        onClick={() => (wide ? setSelectedId(account.id) : openCard(account.id))}
                        {...(wide ? { onActivate: () => openCard(account.id) } : {})}
                        selected={wide && account.id === selectedId}
                        cells={{
                          name: (
                            <div className="ellipsis">
                              <span className="bss-name">{account.customerName}</span>
                              {(perCustomer.get(account.customerId) ?? 0) > 1 && (
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
                                account.ratingType === 'prepaid'
                                  ? 'ui.subscribers.prepaid'
                                  : undefined
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
                          products: <span className={active ? '' : 'bss-zero'}>{active}</span>,
                          cycle: account.ratingType === 'prepaid' ? '—' : account.cycleDay,
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
        </div>
        {selected && subscribers.status === 'ok' && (
          <SubscriberInspector
            account={selected}
            active={subscribers.data.activeProducts.get(selected.id) ?? 0}
            onOpen={() => openCard(selected.id)}
          />
        )}
      </div>
    </div>
  );
}
