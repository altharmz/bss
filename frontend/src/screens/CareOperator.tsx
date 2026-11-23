import { Button, TextInput, SelectField } from '../ui/kit';
import { useState } from 'react';
import type { components } from '../api/generated/internal';
import { ApiError, getAll, getJson, postJson, request } from '../api/client';
import { useResource } from '../api/useResource';

type Login = components['schemas']['CareLogin'];
type Signup = components['schemas']['CareSignup'];
const base = '/internal/v1/careSignup';
const states: Signup['state'][] = [
  'started',
  'identityPending',
  'identityReview',
  'identityPassed',
  'identityFailed',
  'paid',
  'activating',
  'active',
  'failed',
  'expired',
];

export function LoginPanel({ customerId }: { customerId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Self-care login</summary>
      {open && <LoginEditor customerId={customerId} />}
    </details>
  );
}
function LoginEditor({ customerId }: { customerId: string }) {
  const path = `/internal/v1/customer/${encodeURIComponent(customerId)}/careLogin`;
  const login = useResource(path, async (signal) => {
    try {
      return await getJson<Login>(path, signal);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState('');
  async function save(invite: boolean) {
    setBusy(true);
    setError('');
    try {
      if (invite) await postJson(path, { email });
      else await request('DELETE', path);
      login.retry();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  if (login.status === 'loading') return <p>Loading login…</p>;
  if (login.status === 'error')
    return (
      <p role="alert">
        {login.error.message} <Button onClick={login.retry}>Retry</Button>
      </p>
    );
  return (
    <>
      <p data-ui="ui.subscriber.careLogin.status">
        {login.data ? login.data.email + ' · ' + login.data.status : 'No self-care login'}
      </p>
      {error && <p role="alert">{error}</p>}
      {(!login.data || ['invited', 'disabled'].includes(login.data.status)) && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save(true);
          }}
        >
          <label>
            Invitation email
            <TextInput
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <Button
            type="submit"
            variant="primary"
            data-ui="ui.subscriber.careLogin.invite"
            disabled={busy}
          >
            Invite
          </Button>
        </form>
      )}
      {login.data && login.data.status !== 'disabled' && (
        <Button
          data-ui="ui.subscriber.careLogin.disable"
          disabled={busy}
          onClick={() => void save(false)}
        >
          Disable login
        </Button>
      )}
    </>
  );
}
export function Signups() {
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState('');
  const path = filter ? `${base}?state=${encodeURIComponent(filter)}` : base;
  const list = useResource(path, (signal) => getAll<Signup>(path, signal));
  return (
    <section>
      <h1>Signups</h1>
      <label>
        State
        <SelectField
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value);
            setSelected('');
          }}
        >
          <option value="">All states</option>
          {states.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </SelectField>
      </label>
      {list.status === 'loading' && <p>Loading signups…</p>}
      {list.status === 'error' && (
        <p role="alert">
          {list.error.message} <Button onClick={list.retry}>Retry</Button>
        </p>
      )}
      {list.status === 'ok' && (
        <div data-ui="ui.careSignups.list">
          {list.data.length ? (
            <ul>
              {list.data.map((s) => (
                <li key={s.id}>
                  <Button onClick={() => setSelected(s.id)}>{s.email}</Button> {s.state}
                </li>
              ))}
            </ul>
          ) : (
            <p>No signups match this filter.</p>
          )}
        </div>
      )}
      {selected && <SignupDetail key={selected} id={selected} refresh={list.retry} />}
    </section>
  );
}
function SignupDetail({ id, refresh }: { id: string; refresh: () => void }) {
  const path = `${base}/${encodeURIComponent(id)}`;
  const item = useResource(path, (signal) => getJson<Signup>(path, signal));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function decide(decision: 'approve' | 'reject') {
    setBusy(true);
    setError('');
    try {
      await postJson(`${base}/${encodeURIComponent(id)}/decision`, { decision });
      item.retry();
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  if (item.status === 'loading') return <p>Loading signup…</p>;
  if (item.status === 'error')
    return (
      <p role="alert">
        {item.error.message} <Button onClick={item.retry}>Retry</Button>
      </p>
    );
  const s = item.data;
  return (
    <article data-ui="ui.careSignups.detail">
      <h2>{s.email}</h2>
      <p>
        {s.givenName} {s.familyName}
      </p>
      <p>
        {s.offering.name} · {s.state}
      </p>
      <p>{s.identity?.reason ?? s.failure?.reason ?? s.payment?.declineReason}</p>
      {error && <p role="alert">{error}</p>}
      {s.state === 'identityReview' && (
        <>
          <Button
            data-ui="ui.careSignups.approve"
            disabled={busy}
            onClick={() => void decide('approve')}
          >
            Approve
          </Button>
          <Button
            data-ui="ui.careSignups.reject"
            disabled={busy}
            onClick={() => void decide('reject')}
          >
            Reject
          </Button>
        </>
      )}
    </article>
  );
}
