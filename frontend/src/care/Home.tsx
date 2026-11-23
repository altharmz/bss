import { useEffect, useState } from 'react';
import type { components } from '../api/generated/care';
import { careRequest } from './api';
import { AccountTools } from './AccountTools';
import { AddOns } from './AddOns';
import { formatAmount } from '../format/money';

type Schema = components['schemas'];
interface Overview {
  accounts: Schema['CareAccount'][];
  lines: { line: Schema['CareLine']; usage: Schema['CareUsage'] }[];
}
async function overview(): Promise<Overview> {
  const [accounts, lines] = await Promise.all([
    careRequest<Schema['CareAccount'][]>('/account'),
    careRequest<Schema['CareLine'][]>('/line'),
  ]);
  return {
    accounts,
    lines: await Promise.all(
      lines.map(async (line) => ({
        line,
        usage: await careRequest<Schema['CareUsage']>(
          '/line/' + encodeURIComponent(line.id) + '/usage',
        ),
      })),
    ),
  };
}
export function Home() {
  const [data, setData] = useState<Overview>();
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let current = true;
    overview().then(
      (result) => {
        if (current) setData(result);
      },
      (cause) => {
        if (current) setError(String(cause));
      },
    );
    return () => {
      current = false;
    };
  }, [version]);
  if (error)
    return (
      <>
        <p role="alert">{error}</p>
        <button
          onClick={() => {
            setError('');
            setVersion(version + 1);
          }}
        >
          Retry
        </button>
      </>
    );
  if (!data) return <p role="status">Loading your lines and balance…</p>;
  return (
    <>
      <h1 data-ui="ui.care.home">Your account</h1>
      {data.accounts.map((account) => (
        <section key={account.id}>
          <h2>{account.name}</h2>
          {account.balance && (
            <p>
              Available balance{' '}
              <strong>
                {formatAmount(account.balance.spendable.value)} {account.balance.spendable.unit}
              </strong>
            </p>
          )}
          <AccountTools account={account} refresh={() => setVersion((v) => v + 1)} />
        </section>
      ))}
      <h2>Your lines</h2>
      {!data.lines.length && <p>No prepaid lines yet.</p>}
      {data.lines.map(({ line, usage }) => (
        <article key={line.id}>
          <h3>{line.number ?? line.plan.name}</h3>
          <p>
            {line.plan.name} · {line.status}
          </p>
          {line.paidThrough && <p>Renewal date {line.paidThrough}</p>}
          {usage.allowances.map((a) => (
            <label key={a.zone + a.kind}>
              {a.zone} {a.kind} · {a.used} of {a.included} {a.unit}
              {a.percent !== null && <progress value={Math.min(a.percent, 100)} max={100} />}
            </label>
          ))}
          <details>
            <summary>Line details</summary>
            <p>Renewal mode: {line.renewalMode}</p>
            <h4>Add-ons</h4>
            {line.addOns.length ? (
              <ul>
                {line.addOns.map((a) => (
                  <li key={a.id}>{a.name}</li>
                ))}
              </ul>
            ) : (
              <p>No active add-ons.</p>
            )}
            {line.offeringKind === 'plan' && (
              <AddOns line={line} refresh={() => setVersion((v) => v + 1)} />
            )}
          </details>
        </article>
      ))}
    </>
  );
}
