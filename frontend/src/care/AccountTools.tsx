import { useEffect, useState } from 'react';
import { AMOUNT, amountSlot, jsonWithAmount, jsonWithAmounts } from '../api/client';
import type { components } from '../api/generated/care';
import { parseAmount, formatAmount } from '../format/money';
import { useSubmissionKey } from '../screens/submissionKey';
import { CareError, careRequest } from './api';
import { Gateway } from './Gateway';
type S = components['schemas'];
function amount(value: FormDataEntryValue | null) {
  const parsed = parseAmount(String(value));
  if (!parsed || parsed === '0.00')
    throw new Error('Enter a positive amount with at most two decimal places.');
  return parsed;
}
export function AccountTools({
  account,
  refresh,
}: {
  account: S['CareAccount'];
  refresh: () => void;
}) {
  const [cards, setCards] = useState<S['CareCard'][]>();
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [token, setToken] = useState('');
  const key = useSubmissionKey();
  const billingAccountId = account.id;
  const unit = account.currency;
  useEffect(() => {
    let active = true;
    careRequest<S['CareCard'][]>('/paymentMethod').then(
      (rows) => {
        if (active) setCards(rows.filter((r) => r.billingAccount.id === billingAccountId));
      },
      (e) => {
        if (active) setError(String(e));
      },
    );
    return () => {
      active = false;
    };
  }, [billingAccountId, version]);
  async function write(path: string, method: string, build: () => unknown, message: string) {
    if (busy) return;
    setBusy(true);
    setNotice('');
    setError('');
    try {
      const body = build();
      await careRequest(
        path,
        method,
        body,
        method === 'POST' ? key.keyFor(JSON.stringify([path, body])) : undefined,
      );
      key.settle();
      setNotice(message);
      setToken('');
      setVersion((v) => v + 1);
      refresh();
    } catch (e) {
      if (e instanceof CareError && e.status < 500) key.settle();
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <History id={billingAccountId} />
      {account.ratingType === 'prepaid' && (
        <fieldset disabled={busy}>
          <legend>Payments</legend>
          {error && <p role="alert">{error}</p>}
          {notice && <p role="status">{notice}</p>}
          <details>
            <summary>Payment methods</summary>
            {cards && !cards.length && <p>No saved cards.</p>}
            {cards?.map((c) => (
              <p key={c.id}>
                {c.brand} ending in {c.lastFourDigits}{' '}
                <button
                  onClick={() =>
                    void write(
                      '/paymentMethod/' + encodeURIComponent(c.id),
                      'DELETE',
                      () => undefined,
                      'Card removed.',
                    )
                  }
                >
                  Remove
                </button>
              </p>
            ))}
            <Gateway onToken={setToken} />
            <button
              disabled={!token}
              onClick={() =>
                void write(
                  '/paymentMethod',
                  'POST',
                  () => ({ billingAccountId, token }),
                  'Card saved.',
                )
              }
            >
              Save card
            </button>
          </details>
          {cards && cards.length > 0 && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                void write(
                  '/topUp',
                  'POST',
                  () =>
                    jsonWithAmount(
                      { amount: { unit, value: AMOUNT }, paymentMethodId: f.get('card') },
                      amount(f.get('amount')),
                    ),
                  'Top-up submitted. Check history for confirmation.',
                );
              }}
            >
              <label>
                Top-up amount ({unit})<input inputMode="decimal" required name="amount" />
              </label>
              <label>
                Saved card
                <select name="card">
                  {cards.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.brand} {c.lastFourDigits}
                    </option>
                  ))}
                </select>
              </label>
              <button>Top up</button>
            </form>
          )}
          <details>
            <summary>Auto top-up · {account.autoTopUp?.enabled ? 'On' : 'Off'}</summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                void write(
                  '/autoTopUp',
                  'PUT',
                  () =>
                    jsonWithAmounts(
                      {
                        enabled: true,
                        billingAccountId,
                        threshold: { unit, value: amountSlot(0) },
                        amount: { unit, value: amountSlot(1) },
                      },
                      [amount(f.get('threshold')), amount(f.get('amount'))],
                    ),
                  'Auto top-up enabled.',
                );
              }}
            >
              <label>
                Balance threshold ({unit})
                <input
                  inputMode="decimal"
                  name="threshold"
                  required
                  defaultValue={account.autoTopUp?.threshold?.value}
                />
              </label>
              <label>
                Recharge amount ({unit})
                <input
                  inputMode="decimal"
                  name="amount"
                  required
                  defaultValue={account.autoTopUp?.amount?.value}
                />
              </label>
              <button>Enable auto top-up</button>
            </form>
            {account.autoTopUp?.enabled && (
              <button
                onClick={() =>
                  void write(
                    '/autoTopUp',
                    'PUT',
                    () => ({ enabled: false, billingAccountId }),
                    'Auto top-up disabled.',
                  )
                }
              >
                Disable auto top-up
              </button>
            )}
          </details>
        </fieldset>
      )}
    </div>
  );
}
function History({ id }: { id: string }) {
  const [rows, setRows] = useState<S['CareTransaction'][]>();
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    let current = true;
    careRequest<S['CareTransaction'][]>(
      `/history?billingAccountId=${encodeURIComponent(id)}&offset=${offset}&limit=20`,
    ).then(
      (r) => {
        if (current) {
          setRows(r);
          setError('');
        }
      },
      (e) => {
        if (current) setError(String(e));
      },
    );
    return () => {
      current = false;
    };
  }, [id, offset, open]);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>History</summary>
      {error && <p role="alert">{error}</p>}
      {rows?.length === 0 && <p>No transactions.</p>}
      <ul>
        {rows?.map((r) => (
          <li key={r.id}>
            {r.date} · {r.type} · {formatAmount(r.amount.value)} {r.amount.unit}
          </li>
        ))}
      </ul>
      {offset > 0 && <button onClick={() => setOffset(offset - 20)}>Previous</button>}
      {rows?.length === 20 && <button onClick={() => setOffset(offset + 20)}>Next</button>}
    </details>
  );
}
