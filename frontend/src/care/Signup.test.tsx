import { render, screen, fireEvent } from '@testing-library/react';
import { it, expect } from 'vitest';
import { json, tmfError, stubFetch } from '../test/fetch';
import { Signup } from './Signup';
import { expectUi } from '../test/expectUi';

it('offers on-sale plans by name and sends the selected id with the email', async () => {
  const { fetchMock } = stubFetch({
    'GET /self-care/v1/signup/plans': () => json([{ id: 'plan2', name: 'Family prepaid' }]),
    'POST /self-care/v1/signup': () => tmfError(422, 'Plan is no longer on sale'),
  });
  render(<Signup back={() => {}} />);
  expectUi('ui.care.signup');
  fireEvent.change(await screen.findByLabelText('Plan'), { target: { value: 'plan2' } });
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@example.test' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send email code' }));
  expect((await screen.findByRole('alert')).textContent).toContain('Plan is no longer on sale');
  expect(JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body))).toEqual({
    email: 'a@example.test',
    offeringId: 'plan2',
  });
});

it('resumes an unverified signup and keeps the code form after a refusal', async () => {
  sessionStorage.setItem('care.signup.id', 'signup1');
  try {
    stubFetch({
      'GET /self-care/v1/signup/signup1': () =>
        json({ id: 'signup1', state: 'started', emailVerified: false }),
      'POST /self-care/v1/signup/signup1/verify': () => tmfError(401, 'Code expired'),
    });
    render(<Signup back={() => {}} />);
    fireEvent.change(await screen.findByLabelText('Email code'), { target: { value: '000000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Code expired');
    expect(screen.getByLabelText('Email code')).toBeTruthy();
  } finally {
    sessionStorage.removeItem('care.signup.id');
  }
});
