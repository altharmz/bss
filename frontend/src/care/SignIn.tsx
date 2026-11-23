import { useState, type FormEvent } from 'react';
import { careRequest } from './api';

type Step = 'signIn' | 'verify' | 'forgot' | 'password';
const titles = {
  signIn: 'Sign in',
  verify: 'Verify email',
  forgot: 'Forgot password',
  password: 'Set password',
};

export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [step, setStep] = useState<Step>('signIn');
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  function go(next: Step) {
    setError('');
    setNotice('');
    setStep(next);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError('');
    setNotice('');
    try {
      if (step === 'signIn') {
        await careRequest('/session', 'POST', { email, password: data.get('password') });
        go('verify');
        setNotice('Enter the code sent to your email.');
      } else if (step === 'verify') {
        await careRequest('/session/verify', 'POST', { email, code: data.get('code') });
        onSignedIn();
      } else if (step === 'forgot') {
        await careRequest('/password/reset', 'POST', { email });
        go('password');
        setNotice('If your email is registered, check it for a password reset code.');
      } else {
        await careRequest('/password', 'POST', {
          code: data.get('code'),
          newPassword: data.get('password'),
        });
        go('signIn');
        setNotice('Password saved. Sign in with your new password.');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Request failed');
    } finally {
      setBusy(false);
    }
  }
  return (
    <section data-ui="ui.care.signIn">
      <h1>{titles[step]}</h1>
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      <form key={step} onSubmit={submit}>
        <fieldset disabled={busy}>
          {(step === 'signIn' || step === 'forgot') && (
            <label>
              Email
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
          )}
          {(step === 'verify' || step === 'password') && (
            <label>
              Email code
              <input name="code" autoComplete="one-time-code" required />
            </label>
          )}
          {(step === 'signIn' || step === 'password') && (
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete={step === 'signIn' ? 'current-password' : 'new-password'}
                required
              />
            </label>
          )}
          <button>{step === 'forgot' ? 'Send reset code' : titles[step]}</button>
        </fieldset>
      </form>
      {step === 'signIn' ? (
        <>
          <button disabled={busy} onClick={() => go('forgot')}>
            Forgot password
          </button>
          <button disabled={busy} onClick={() => go('password')}>
            Set password with invite code
          </button>
        </>
      ) : (
        <button disabled={busy} onClick={() => go('signIn')}>
          Back to sign in
        </button>
      )}
    </section>
  );
}
