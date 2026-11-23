import { useEffect, useState, type FormEvent } from 'react';
import type { components } from '../api/generated/care';
import { QrCode } from '../ui/QrCode';
import { careRequest } from './api';
import { Gateway } from './Gateway';
type RecordView = components['schemas']['CareSignup'];
const storageKey = 'care.signup.id';
const detailsFields = [
  ['givenName', 'First name', 'given-name'],
  ['familyName', 'Last name', 'family-name'],
  ['birthDate', 'Date of birth', 'bday'],
  ['street1', 'Street address', 'address-line1'],
  ['city', 'City', 'address-level2'],
  ['stateOrProvince', 'State (two letters)', 'address-level1'],
  ['postCode', 'ZIP code', 'postal-code'],
] as const;
export function Signup({ back }: { back: () => void }) {
  const [record, setRecord] = useState<RecordView>();
  const [resume, setResume] = useState(() => sessionStorage.getItem(storageKey));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState('');
  const [retry, setRetry] = useState(0);
  const [plans, setPlans] = useState<RecordView['offering'][]>();
  const starting = !record && !resume;
  useEffect(() => {
    if (!starting) return;
    let current = true;
    careRequest<RecordView['offering'][]>('/signup/plans').then(
      (choices) => {
        if (current) {
          setPlans(choices);
          setError('');
        }
      },
      (cause) => {
        if (current) setError(String(cause));
      },
    );
    return () => {
      current = false;
    };
  }, [starting, retry]);
  const path = record ? '/signup/' + encodeURIComponent(record.id) : '';
  const waiting =
    record &&
    (['identityPending', 'identityReview', 'paid', 'activating'].includes(record.state) ||
      (record.state === 'identityPassed' &&
        ['pending', 'sent', 'confirmed'].includes(record.payment?.state ?? '')));
  useEffect(() => {
    const read = resume ? '/signup/' + encodeURIComponent(resume) : waiting ? path : '';
    if (!read) return;
    let current = true;
    const timer = setTimeout(
      () => {
        careRequest<RecordView>(read).then(
          (r) => {
            if (current) {
              setRecord(r);
              setResume(null);
              setError('');
              setRetry((n) => n + 1);
            }
          },
          (e) => {
            if (current) setError(String(e));
          },
        );
      },
      resume ? 0 : 3000,
    );
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [resume, path, waiting, retry]);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy) return;
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError('');
    try {
      let result: RecordView;
      if (!record) {
        result = await careRequest('/signup', 'POST', {
          email: f.get('email'),
          offeringId: f.get('plan'),
        });
        sessionStorage.setItem(storageKey, result.id);
      } else if (!record.emailVerified) {
        result = await careRequest(path + '/verify', 'POST', { code: f.get('code') });
      } else if (record.state === 'started') {
        const { givenName, familyName, birthDate, ...serviceAddress } = Object.fromEntries(f);
        result = await careRequest(path + '/details', 'PUT', {
          givenName,
          familyName,
          birthDate,
          serviceAddress,
        });
      } else {
        result = await careRequest(path + '/payment', 'POST', {
          token,
          password: f.get('password'),
        });
        setToken('');
      }
      setRecord(result);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }
  function finish() {
    sessionStorage.removeItem(storageKey);
    back();
  }
  const stage = !record ? 'start' : !record.emailVerified ? 'code' : record.state;
  return (
    <section data-ui="ui.care.signup">
      <h1>Sign up</h1>
      {error && (
        <p role="alert">
          {error}{' '}
          {(resume || waiting || !plans) && (
            <button onClick={() => setRetry((n) => n + 1)}>Retry</button>
          )}
        </p>
      )}
      {resume ? (
        <p>Opening signup…</p>
      ) : (
        <form key={stage} onSubmit={submit}>
          <fieldset disabled={busy}>
            {!record && (
              <>
                <label>
                  Email
                  <input type="email" autoComplete="email" name="email" required />
                </label>
                <div>
                  <label htmlFor="signup-plan">Plan</label>
                  {plans && (
                    <select
                      id="signup-plan"
                      name="plan"
                      defaultValue={new URLSearchParams(location.search).get('plan') ?? ''}
                      required
                    >
                      <option value="">Select a plan</option>
                      {plans.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                {plans?.length === 0 && <p>No plans are currently available.</p>}
                <button disabled={!plans?.length}>Send email code</button>
              </>
            )}
            {stage === 'code' && (
              <>
                <label>
                  Email code
                  <input name="code" autoComplete="one-time-code" required />
                </label>
                <button>Verify email</button>
              </>
            )}
            {stage === 'started' && (
              <>
                {detailsFields.map(([name, label, complete]) => (
                  <label key={name}>
                    {label}
                    <input
                      type={name === 'birthDate' ? 'date' : 'text'}
                      name={name}
                      autoComplete={complete}
                      required
                    />
                  </label>
                ))}
                <button>Verify identity</button>
              </>
            )}
            {waiting && (
              <p role="status">Signup status · {record?.state}. This page updates automatically.</p>
            )}
            {stage === 'identityPassed' && !waiting && (
              <>
                <p>Plan · {record?.offering.name}</p>
                {record?.payment?.declineReason && <p>{record.payment.declineReason}</p>}
                <Gateway onToken={setToken} />
                <label>
                  Create password
                  <input type="password" autoComplete="new-password" name="password" required />
                </label>
                <button disabled={!token}>Pay and activate</button>
              </>
            )}
            {stage === 'active' && (
              <>
                <h2>Your line is active</h2>
                {record?.esim ? (
                  <>
                    <p>{record.esim.activationCode}</p>
                    <QrCode text={record.esim.qrPayload} label="eSIM activation code" />
                  </>
                ) : (
                  <p>Your activation code was emailed to you.</p>
                )}
              </>
            )}
            {['identityFailed', 'failed', 'expired'].includes(stage) && (
              <p>
                Signup {stage}. {record?.failure?.reason ?? record?.identity?.reason}
              </p>
            )}
          </fieldset>
        </form>
      )}
      <button
        disabled={busy}
        onClick={
          (record && ['active', 'failed', 'identityFailed', 'expired'].includes(stage)) ||
          (resume && error)
            ? finish
            : back
        }
      >
        Back to sign in
      </button>
    </section>
  );
}
