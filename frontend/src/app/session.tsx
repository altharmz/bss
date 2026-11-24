import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import type { components } from '../api/generated/internal';
import { ApiError, explain, getJson, postJson, request } from '../api/client';
import { Button, Panel, TextInput } from '../ui/kit';
import { useRouter } from './router';

export type Session = components['schemas']['Session'];
const PATH = '/internal/v1/session';
const SessionContext = createContext<Session | null>(null);
export function useSession() {
  return useContext(SessionContext);
}

function returnPath() {
  const path = new URLSearchParams(window.location.search).get('next');
  return path &&
    path.startsWith('/') &&
    !path.startsWith('//') &&
    !/[\\\s]/.test(path) &&
    !/^\/(sign-in|change-password)([/?#]|$)/.test(path)
    ? path
    : '/billing-accounts';
}

function signInPath() {
  const next =
    window.location.pathname === '/sign-in'
      ? returnPath()
      : window.location.pathname + window.location.search;
  return `/sign-in?next=${encodeURIComponent(next)}`;
}

export function SessionGate({ children }: { children: ReactNode }) {
  const { route, navigate } = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const signOut = useCallback(() => {
    const path = signInPath();
    setSession(null);
    navigate(path);
  }, [navigate]);
  useEffect(() => {
    let active = true;
    getJson<Session>(PATH)
      .then((value) => {
        if (active) setSession(value);
      })
      .catch((e: unknown) => {
        if (!active) return;
        if (e instanceof ApiError && e.status === 401) signOut();
        else setError(explain(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [signOut]);
  useEffect(() => {
    const expired = () => signOut();
    window.addEventListener('bss:unauthorized', expired);
    return () => window.removeEventListener('bss:unauthorized', expired);
  }, [signOut]);
  useEffect(() => {
    if (session && route.name === 'signIn') navigate(returnPath());
    if (!loading && !error && !session && route.name !== 'signIn') navigate(signInPath());
  }, [session, loading, error, route.name, navigate]);
  if (loading) return <p role="status">Checking session…</p>;
  if (error)
    return (
      <Panel>
        <p role="alert">{error}</p>
        <Button onClick={() => window.location.reload()}>Retry</Button>
      </Panel>
    );
  if (!session)
    return (
      <CredentialForm
        kind="signIn"
        onSuccess={(value) => {
          if (value) setSession(value);
        }}
      />
    );
  if (route.name === 'signIn') return <p role="status">Opening your session…</p>;
  return (
    <SessionContext.Provider value={session}>
      {session.mustChangePassword || route.name === 'password' ? (
        <CredentialForm
          kind="password"
          onSuccess={() => signOut()}
          actions={
            <>
              {!session.mustChangePassword && (
                <Button onClick={() => navigate('/billing-accounts')}>Cancel</Button>
              )}
              <SignOut />
            </>
          }
        />
      ) : (
        children
      )}
    </SessionContext.Provider>
  );
}

export function SignOut() {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError('');
          try {
            await request('DELETE', PATH);
            window.dispatchEvent(new Event('bss:unauthorized'));
          } catch (e) {
            setError(explain(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        Sign out
      </Button>
      {error && <p role="alert">{error}</p>}
    </>
  );
}

function CredentialForm({
  kind,
  onSuccess,
  actions,
}: {
  kind: 'signIn' | 'password';
  onSuccess: (value?: Session) => void;
  actions?: ReactNode;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const changing = kind === 'password';
  return (
    <main className="bss-auth">
      <Panel>
        <form
          className="bss-auth-form"
          onSubmit={async (event) => {
            event.preventDefault();
            setError('');
            if (changing && next !== confirm) {
              setError('Passwords do not match');
              return;
            }
            setBusy(true);
            try {
              if (changing) {
                await request('POST', PATH + '/password', {
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ currentPassword: password, newPassword: next }),
                });
                onSuccess();
              } else onSuccess(await postJson<Session>(PATH, { username, password }));
            } catch (e) {
              setError(explain(e));
              setPassword('');
              setNext('');
              setConfirm('');
            } finally {
              setBusy(false);
            }
          }}
        >
          {changing ? (
            <h1 data-ui="ui.session.password">Change password</h1>
          ) : (
            <h1 data-ui="ui.session.signIn">Sign in</h1>
          )}
          {changing && (
            <p>
              Use at least 12 characters, different from your username. Sign in again after changing
              your password.
            </p>
          )}
          {!changing && (
            <label>
              Username
              <TextInput
                required
                autoComplete="username"
                maxLength={64}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
          )}
          <label>
            {changing ? 'Current password' : 'Password'}
            <TextInput
              required
              type="password"
              autoComplete="current-password"
              maxLength={1024}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {changing && (
            <>
              <label>
                New password
                <TextInput
                  required
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  maxLength={1024}
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                />
              </label>
              <label>
                Confirm password
                <TextInput
                  required
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                />
              </label>
            </>
          )}
          {error && <p role="alert">{error}</p>}
          <Button type="submit" variant="primary" disabled={busy}>
            {changing ? 'Change password' : 'Sign in'}
          </Button>
          {actions}
        </form>
      </Panel>
    </main>
  );
}
