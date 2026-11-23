import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { CareError, careRequest } from './api';
import { SignIn } from './SignIn';
import { Home } from './Home';
import { Signup } from './Signup';
import './care.css';

function Care() {
  const [signup, setSignup] = useState(false);
  const [session, setSession] = useState<'loading' | 'guest' | 'customer' | 'unavailable'>(
    'loading',
  );
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    careRequest('/session').then(
      () => setSession('customer'),
      (cause) => {
        if (cause instanceof CareError && cause.status === 401) setSession('guest');
        else {
          setError(
            cause instanceof CareError && cause.status === 404
              ? 'Self-care is unavailable.'
              : String(cause),
          );
          setSession('unavailable');
        }
      },
    );
  }, [attempt]);
  if (session === 'unavailable')
    return (
      <>
        <p role="alert">{error}</p>
        <button
          onClick={() => {
            setError('');
            setSession('loading');
            setAttempt((n) => n + 1);
          }}
        >
          Retry
        </button>
      </>
    );
  if (session === 'loading') return <p role="status">Opening your account…</p>;
  if (session === 'guest')
    return signup ? (
      <Signup back={() => setSignup(false)} />
    ) : (
      <>
        <SignIn onSignedIn={() => setSession('customer')} />
        <button onClick={() => setSignup(true)}>Sign up for a line</button>
      </>
    );
  return (
    <>
      <header>
        <strong>Customer self-care</strong>
        <button
          onClick={async () => {
            try {
              await careRequest('/session', 'DELETE');
              setError('');
              setSession('guest');
            } catch (cause) {
              setError(String(cause));
            }
          }}
        >
          Sign out
        </button>
      </header>
      {error && <p role="alert">{error}</p>}
      <Home />
    </>
  );
}
const root = document.getElementById('root');
if (root) createRoot(root).render(<Care />);
