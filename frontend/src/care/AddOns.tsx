import { useEffect, useState } from 'react';
import type { components } from '../api/generated/care';
import { useSubmissionKey } from '../screens/submissionKey';
import { CareError, careRequest } from './api';
type S = components['schemas'];
export function AddOns({ line, refresh }: { line: S['CareLine']; refresh: () => void }) {
  const [offers, setOffers] = useState<S['CareOffering'][]>();
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const keys = useSubmissionKey();
  const account = line.billingAccount.id;
  useEffect(() => {
    let current = true;
    careRequest<S['CareOffering'][]>(
      '/offering?kind=addOn&billingAccountId=' + encodeURIComponent(account),
    ).then(
      (r) => {
        if (current) setOffers(r);
      },
      (e) => {
        if (current) setError(String(e));
      },
    );
    return () => {
      current = false;
    };
  }, [account]);
  async function buy(offeringId: string) {
    setBusy(true);
    setMessage('');
    setError('');
    const body = { lineId: line.id, offeringId };
    try {
      await careRequest('/addOn', 'POST', body, keys.keyFor(JSON.stringify(body)));
      keys.settle();
      setMessage('Add-on order submitted.');
      refresh();
    } catch (e) {
      if (e instanceof CareError && e.status < 500) keys.settle();
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {offers?.length === 0 && <p>No add-ons available to buy.</p>}
      {offers?.map((o) => (
        <p key={o.id}>
          <button disabled={busy} onClick={() => void buy(o.id)}>
            Buy {o.name} · {o.firstCharge.amount.value} {o.firstCharge.amount.unit}
          </button>
        </p>
      ))}
    </div>
  );
}
