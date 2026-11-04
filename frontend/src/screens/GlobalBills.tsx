import { useState } from 'react';
import {
  BILL_STATES,
  loadAllBills,
  loadBillCycles,
  type BillRow,
  type BillStateFilter,
} from '../api/bills';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { businessDate } from '../format/dates';
import { accountLabel } from '../format/ids';
import { formatInt, toCents } from '../format/money';
import { Amount } from '../ui/Amount';
import { PageHeader, Panel, StatusPill, Segmented, TextInput } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { BillDialog } from './Bills';
import { CycleDialog } from './BillLinks';
import './screens.css';

// Global bills and bill cycles (docs/ui.md): TMF678 reads, plus
// the existing bill dialog (hold/release PATCH). State and account filter on the server; the
// billing date is applied client side to the loaded rows.

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
  const bills = useResource(`bills-all:${account ?? ''}:${state}:${version}`, (signal) =>
    loadAllBills(filter, signal),
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
      ? (bills.data.find((b) => b.billingAccount.id === account)?.billingAccount.name ?? null)
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
              ...BILL_STATES.map((s) => ({ key: s, label: s })),
            ]}
          />
        )}
        <label className="bss-caption" htmlFor="bills-date">
          Billing date
        </label>
        <TextInput
          id="bills-date"
          type="date"
          mono
          value={date}
          onChange={(event) => setDate(event.target.value)}
        />
        {tab === 'bills' && account !== null && (
          <span className="bss-run-tag" data-ui="ui.bills.accountChip">
            Account {name ?? accountLabel(account)}{' '}
            <button
              type="button"
              className="bss-link"
              aria-label="Remove the account filter"
              onClick={dropAccount}
            >
              ×
            </button>
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
              total={(data) => data.length}
              rows={(data) =>
                data
                  .filter((b) => onDate(b.billDate))
                  .sort((a, b) => b.billDate.localeCompare(a.billDate))
              }
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
                              {bill.billingAccount.name ?? accountLabel(bill.billingAccount.id)}
                            </Link>
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
                        kind: bill.runType ?? '—',
                      }}
                    />
                  ))}
                  <TableFooter left={`Showing ${shown(rows.length, data.length)}`} />
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
              total={(data) => data.length}
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
