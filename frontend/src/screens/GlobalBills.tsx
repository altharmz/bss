import { useState } from 'react';
import {
  BILL_PAGE,
  BILL_STATES,
  loadBillPage,
  loadBillCycles,
  type BillRow,
  type BillStateFilter,
} from '../api/bills';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate } from '../format/dates';
import { Id } from '../ui/Id';
import { formatInt, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { runTriggerLabel } from '../format/labels';
import { statusPresentation } from '../ui/status';
import { Coded, PageHeader, Panel, StatusPill, Segmented, DateField, IconButton } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { PageFooter, usePageOffset } from '../ui/paging';
import { BillDialog } from './Bills';
import { CycleDialog } from './BillLinks';
import './screens.css';

// Global bills and bill cycles (docs/ui.md): TMF678 reads, plus
// the existing bill dialog (hold/release PATCH). Bills page on the server, newest first,
// with the state and account filters there (#314); the API has no billing-date filter,
// so the date filters only the bill cycles (one per billing date, read whole).

const BILL_COLUMNS: Column[] = [
  { key: 'no', label: 'Bill', width: '120px', mono: true },
  { key: 'account', label: 'Subscriber', width: 'minmax(0, 1fr)' },
  { key: 'date', label: 'Billing date', width: '110px', mono: true },
  { key: 'state', label: 'State', width: '130px' },
  { key: 'amount', label: 'Total', width: '120px', align: 'right', mono: true },
  { key: 'kind', label: 'Kind', width: '90px' },
];

const CYCLE_COLUMNS: Column[] = [
  { key: 'name', label: 'Bill cycle', width: 'minmax(0, 1fr)' },
  { key: 'date', label: 'Billing date', width: '120px', mono: true },
  { key: 'due', label: 'Payment due', width: '120px', mono: true },
];

type Tab = 'bills' | 'cycles';
type StateFilter = BillStateFilter | 'all';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function accountFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get('account');
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

/** The global Bills screen: Bills and Bill cycles tabs. */
export function BillsScreen() {
  useSetCrumbs([{ label: 'Bills' }], 'bills');
  const { timeZone } = useSystemContext();
  const [tab, setTab] = useState<Tab>('bills');
  const [state, setState] = useState<StateFilter>('all');
  const [account, setAccount] = useState<string | null>(accountFromUrl);
  const [date, setDate] = useState('');
  const [version, setVersion] = useState(0);
  const [openBill, setOpenBill] = useState<BillRow | null>(null);
  const [openCycle, setOpenCycle] = useState<string | null>(null);
  const filter = { account, state: state === 'all' ? null : state };
  const filterKey = `${account ?? ''}:${state}:${version}`;
  const [offset, setOffset] = usePageOffset(filterKey);
  const bills = useResource(`bills-all:${filterKey}:${offset}`, (signal) =>
    loadBillPage(filter, offset, signal),
  );
  const cycles = useResource('bill-cycles', (signal) => loadBillCycles(signal));

  const dropAccount = () => {
    window.history.replaceState(null, '', '/bills');
    setAccount(null);
  };
  const reset = () => {
    setState('all');
    setDate('');
    if (account !== null) dropAccount();
  };
  const name =
    bills.status === 'ok'
      ? (bills.data.items.find((b) => b.billingAccount.id === account)?.billingAccount.name ?? null)
      : null;
  const onDate = (instant: string) => date === '' || businessDate(instant, timeZone) === date;
  const shown = (rows: number, all: number) =>
    rows === all ? `${formatInt(rows)}` : `${formatInt(rows)} of ${formatInt(all)}`;

  return (
    <div className="bss-screen" data-ui="ui.bills">
      <div className="bss-title-row">
        <PageHeader title="Bills" />
        <span className="bss-caption">
          Every account's bills and the billing cycles, newest first
        </span>
      </div>
      <div className="bss-toolbar">
        <Segmented
          label="Kind"
          value={tab}
          onChange={setTab}
          options={[
            { key: 'bills', label: 'Bills' },
            { key: 'cycles', label: 'Bill cycles' },
          ]}
        />
        {tab === 'bills' && (
          <Segmented
            label="State"
            value={state}
            onChange={setState}
            options={[
              { key: 'all', label: 'All' },
              ...BILL_STATES.map((s) => ({
                key: s,
                label: statusPresentation('bill', s)[1],
                title: s,
              })),
            ]}
          />
        )}
        {tab === 'cycles' && (
          <>
            <label className="bss-caption" htmlFor="bills-date">
              Billing date
            </label>
            <DateField
              id="bills-date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
          </>
        )}
        {tab === 'bills' && account !== null && (
          <span className="bss-run-tag" data-ui="ui.bills.accountChip">
            Account {name ?? <Id id={account} prefix="BA-" />}{' '}
            <IconButton icon="x" small label="Remove the account filter" onClick={dropAccount} />
          </span>
        )}
      </div>
      <Panel>
        {tab === 'bills' ? (
          <>
            <TableHead columns={BILL_COLUMNS} />
            <ListState
              resource={bills}
              columns={BILL_COLUMNS}
              errorTitle="Failed to load bills"
              empty={{ title: 'No bills', text: 'A bill is issued on the billing cycle day.' }}
              // filtered on the server: an empty filtered page is "no matches"
              count={(data) => (state !== 'all' || account !== null ? 1 : data.items.length)}
              rows={(data) => data.items}
              onResetFilters={reset}
            >
              {(rows, data) => (
                <>
                  {rows.map((bill) => (
                    <TableRow
                      key={bill.id}
                      columns={BILL_COLUMNS}
                      onClick={() => setOpenBill(bill)}
                      cells={{
                        no: bill.billNo,
                        account: (
                          <span className="ellipsis" onClick={(e) => e.stopPropagation()}>
                            <Link
                              className="bss-link"
                              href={hrefOf({
                                name: 'subscriber',
                                accountId: bill.billingAccount.id,
                              })}
                            >
                              {bill.billingAccount.name ?? 'Account'}
                            </Link>{' '}
                            <Id id={bill.billingAccount.id} prefix="BA-" />
                          </span>
                        ),
                        date: businessDate(bill.billDate, timeZone),
                        state: <StatusPill kind="bill" status={bill.state} />,
                        amount: (
                          <Amount
                            cents={toCents(bill.amountDue.value)}
                            currency={bill.amountDue.unit}
                          />
                        ),
                        kind: bill.runType ? (
                          <Coded code={bill.runType} label={runTriggerLabel(bill.runType)} />
                        ) : (
                          '—'
                        ),
                      }}
                    />
                  ))}
                  <PageFooter
                    offset={data.offset}
                    shown={rows.length}
                    more={data.more}
                    size={BILL_PAGE}
                    onOffset={setOffset}
                    ui="ui.bills"
                  />
                </>
              )}
            </ListState>
          </>
        ) : (
          <>
            <TableHead columns={CYCLE_COLUMNS} />
            <ListState
              resource={cycles}
              columns={CYCLE_COLUMNS}
              errorTitle="Failed to load bill cycles"
              empty={{
                title: 'No bill cycles',
                text: 'A cycle exists for each daily billing run.',
              }}
              count={(data) => data.length}
              rows={(data) =>
                data
                  .filter((c) => onDate(c.billingDate))
                  .sort((a, b) => b.billingDate.localeCompare(a.billingDate))
              }
              onResetFilters={reset}
            >
              {(rows, data) => (
                <>
                  {rows.map((cycle) => (
                    <TableRow
                      key={cycle.id}
                      columns={CYCLE_COLUMNS}
                      onClick={() => setOpenCycle(cycle.id)}
                      cells={{
                        name: cycle.name,
                        date: businessDate(cycle.billingDate, timeZone),
                        due:
                          cycle.paymentDueDate === undefined
                            ? '—'
                            : businessDate(cycle.paymentDueDate, timeZone),
                      }}
                    />
                  ))}
                  <TableFooter left={`Showing ${shown(rows.length, data.length)}`} />
                </>
              )}
            </ListState>
          </>
        )}
      </Panel>
      {openBill !== null && (
        <BillDialog
          billId={openBill.id}
          cycleId={openBill.billCycle?.id}
          onClose={() => setOpenBill(null)}
          onChanged={() => {
            setOpenBill(null);
            setVersion((v) => v + 1);
          }}
        />
      )}
      {openCycle !== null && <CycleDialog cycleId={openCycle} onClose={() => setOpenCycle(null)} />}
    </div>
  );
}
