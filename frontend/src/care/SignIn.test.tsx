import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SignIn } from './SignIn';
import { stubFetch } from '../test/fetch';
import { expectUi } from '../test/expectUi';

it('requests a reset code and sets the new password', async () => {
  const { fetchMock } = stubFetch({
    'POST /self-care/v1/password/reset': () => new Response(null, { status: 204 }),
    'POST /self-care/v1/password': () => new Response(null, { status: 204 }),
  });
  render(<SignIn onSignedIn={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Forgot password' }));
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@example.test' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send reset code' }));
  fireEvent.change(await screen.findByLabelText('Email code'), { target: { value: 'reset-code' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'my-new-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Set password' }));
  await screen.findByText('Password saved. Sign in with your new password.');
  expect(fetchMock.mock.calls.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([
    { email: 'a@example.test' },
    { code: 'reset-code', newPassword: 'my-new-password' },
  ]);
});

it('verifies the emailed code before opening the customer account', async () => {
  const request = vi.fn().mockResolvedValue(Response.json({}));
  vi.stubGlobal('fetch', request);
  const signedIn = vi.fn();
  render(<SignIn onSignedIn={signedIn} />);
  expectUi('ui.care.signIn');
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'customer@example.com' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  fireEvent.change(await screen.findByLabelText('Email code'), { target: { value: '012345' } });
  expect(signedIn).not.toHaveBeenCalled();
  request.mockResolvedValue(Response.json({}));
  fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
  await waitFor(() => expect(signedIn).toHaveBeenCalledOnce());
  expect(
    request.mock.calls.map(([path, init]) => [
      path,
      JSON.parse(init.body),
      init.headers.get('X-Bss-Request'),
    ]),
  ).toEqual([
    ['/self-care/v1/session', { email: 'customer@example.com', password: 'secret password' }, '1'],
    ['/self-care/v1/session/verify', { email: 'customer@example.com', code: '012345' }, '1'],
  ]);
});

it('keeps the form available after a refused password', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(Response.json({ message: 'Sign in refused' }, { status: 401 })),
  );
  render(<SignIn onSignedIn={() => {}} />);
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'customer@example.com' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Sign in refused');
  expect(screen.getByLabelText('Password')).toBeTruthy();
});

it('sets a password with an invite code and goes back to sign-in without a trace', async () => {
  const { fetchMock } = stubFetch({
    'POST /self-care/v1/password': () =>
      Response.json({ message: 'Invite code expired' }, { status: 400 }),
  });
  render(<SignIn onSignedIn={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: 'Set password with invite code' }));
  expect(screen.getByRole('heading', { name: 'Set password' })).toBeTruthy();
  // an invite needs no email: only the code and the new password
  expect(screen.queryByLabelText('Email')).toBeNull();
  fireEvent.change(screen.getByLabelText('Email code'), { target: { value: 'invite-1' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'first password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Set password' }));
  expect((await screen.findByRole('alert')).textContent).toBe('Invite code expired');
  expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
    code: 'invite-1',
    newPassword: 'first password',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Back to sign in' }));
  expect(screen.getByRole('heading', { name: 'Sign in' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByRole('button', { name: 'Forgot password' })).toBeTruthy();
});
