import { Fragment } from 'react';
import { loadFinancialAccounts, type ContainedKind } from '../api/bss';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { hrefOf, Link } from '../app/router';
import { shortId } from '../format/ids';
import { formatInt } from '../format/money';
import { Amount } from '../ui/Amount';
import { PageTitle, Panel, Pill } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import './screens.css';

// Read-only (docs/decisions/accounts.md): a financial account groups a party's accounts.
// Links are made on the accounts themselves, through the API or MCP; the balance is the
// sum of the linked billing accounts, one line per currency.

export const COLUMNS: Column[] = [
  { key: 'no', label: 'Account no.', width: '120px', mono: true },
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'owner', label: 'Owner', width: '1fr' },
  { key: 'status', label: 'Status', width: '110px' },
  { key: 'balance', label: 'Balance', width: '170px', align: 'right', mono: true },
  { key: 'contains', label: 'Contains', width: '1.4fr' },
];

const KIND_LABEL: Record<ContainedKind, string> = {
  BillingAccount: 'billing',
  PartyAccount: 'party',
  SettlementAccount: 'settlement',
};

export function FinancialAccountsScreen() {
  useSetCrumbs([{ label: 'Financial accounts' }], 'financialAccounts');
  const accounts = useResource('financialAccounts', loadFinancialAccounts);

  return (
    <div className="bss-screen" data-ui="ui.financialAccounts.list">
      <div className="bss-title-row">
        <PageTitle
          title="Financial accounts"
          count={accounts.status === 'ok' ? formatInt(accounts.data.length) : undefined}
        />
      </div>
      <Panel>
        <TableHead columns={COLUMNS} />
        <ListState
          resource={accounts}
          columns={COLUMNS}
          errorTitle="Failed to load financial accounts"
          empty={{
            title: 'No financial accounts yet',
            text: 'Create one through the TMF666 API, then link billing accounts to it.',
          }}
          total={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {}}
        >
          {(rows) => (
            <>
              {rows.map((account) => (
                <TableRow
                  key={account.id}
                  columns={COLUMNS}
                  cells={{
                    no: <span title={account.id}>{shortId(account.id)}</span>,
                    name: (
                      <div className="ellipsis">
                        <span className="bss-name">{account.name}</span>
                        {account.accountType && (
                          <span className="bss-secondary"> · {account.accountType}</span>
                        )}
                      </div>
                    ),
                    owner: (
                      <div className="ellipsis">
                        {account.ownerName}
                        <span className="bss-secondary"> · {account.ownerKind}</span>
                      </div>
                    ),
                    status: <Pill status={account.state} />,
                    balance:
                      account.balances.length === 0 ? (
                        <span className="bss-zero">—</span>
                      ) : (
                        <div>
                          {account.balances.map((balance) => (
                            <div key={balance.currency}>
                              <Amount cents={balance.cents} currency={balance.currency} chip />
                            </div>
                          ))}
                        </div>
                      ),
                    contains:
                      account.contains.length === 0 ? (
                        <span className="bss-zero">nothing linked</span>
                      ) : (
                        <div className="ellipsis">
                          {account.contains.map((item, i) => (
                            <Fragment key={item.id}>
                              {i > 0 && ', '}
                              {item.kind === 'BillingAccount' ? (
                                <Link
                                  className="bss-link"
                                  href={hrefOf({ name: 'subscriber', accountId: item.id })}
                                >
                                  {item.name}
                                </Link>
                              ) : (
                                item.name
                              )}
                              <span className="bss-secondary"> ({KIND_LABEL[item.kind]})</span>
                            </Fragment>
                          ))}
                        </div>
                      ),
                  }}
                />
              ))}
              <TableFooter
                left={`${formatInt(rows.length)} financial accounts`}
                right="Balance: sum of the linked billing accounts, per currency"
              />
            </>
          )}
        </ListState>
      </Panel>
    </div>
  );
}
