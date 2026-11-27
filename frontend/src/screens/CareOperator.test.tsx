import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { it, expect } from 'vitest';
import { Signups, LoginPanel } from './CareOperator';
import { stubFetch, json, tmfError } from '../test/fetch';
import { expectUi } from '../test/expectUi';

it('filters signups and decides a signup in identity review', async () => {
  const item = {
    id: 'signup1',
    email: 'customer@example.test',
    state: 'identityReview',
    offering: { name: 'Basic' },
    identity: { reason: 'Manual review' },
  };
  let approved = false;
  const { calls } = stubFetch({
    'GET /internal/v1/careSignup?offset=0&limit=100': () => json([]),
    'GET /internal/v1/careSignup?state=identityReview&offset=0&limit=100': () =>
      json(approved ? [] : [item]),
    'GET /internal/v1/careSignup/signup1': () =>
      json({ ...item, state: approved ? 'identityPassed' : item.state }),
    'POST /internal/v1/careSignup/signup1/decision': () => {
      approved = true;
      return json({});
    },
  });
  render(<Signups />);
  await screen.findByText('No signups match this filter.');
  expectUi('ui.careSignups.list');
  fireEvent.change(screen.getByLabelText('State'), { target: { value: 'identityReview' } });
  fireEvent.click(await screen.findByRole('button', { name: item.email }));
  await screen.findByRole('button', { name: 'Approve' });
  expectUi('ui.careSignups.detail');
  expectUi('ui.careSignups.reject');
  fireEvent.click(expectUi('ui.careSignups.approve'));
  await screen.findByText(/Basic · identityPassed/);
  expect(calls).toContain('POST /internal/v1/careSignup/signup1/decision');
});
it('invites a customer with no login and shows a refused disable', async () => {
  let invited = false;
  stubFetch({
    'GET /internal/v1/customer/c1/careLogin': () =>
      invited ? json({ status: 'invited', email: 'a@example.test' }) : tmfError(404, 'No login'),
    'POST /internal/v1/customer/c1/careLogin': () => {
      invited = true;
      return json({});
    },
    'DELETE /internal/v1/customer/c1/careLogin': () => tmfError(409, 'Refused'),
  });
  render(<LoginPanel customerId="c1" />);
  fireEvent.click(screen.getByText('Self-care login'));
  fireEvent.change(await screen.findByLabelText('Invitation email'), {
    target: { value: 'a@example.test' },
  });
  fireEvent.click(expectUi('ui.subscriber.careLogin.invite'));
  await screen.findByText('a@example.test · invited');
  expectUi('ui.subscriber.careLogin.status');
  fireEvent.click(expectUi('ui.subscriber.careLogin.disable'));
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('409'));
});
// #499: a failed load with Retry, a refused decision, and Reject with its exact body
it('shows a failed login load with Retry, then the login', async () => {
  let fail = true;
  stubFetch({
    'GET /internal/v1/customer/c1/careLogin': () =>
      fail ? tmfError(500, 'Internal Server Error') : json({ status: 'active', email: 'a@x.test' }),
  });
  render(<LoginPanel customerId="c1" />);
  fireEvent.click(screen.getByText('Self-care login'));
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('500 Internal Server Error');
  expect(screen.queryByLabelText('Invitation email')).toBeNull();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('a@x.test · active')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});
it('retries a failed signup, shows a refused decision, and rejects with the exact body', async () => {
  const item = {
    id: 'signup1',
    email: 'customer@example.test',
    state: 'identityReview',
    offering: { name: 'Basic' },
    identity: { reason: 'Manual review' },
  };
  let detail = 0;
  let decisions = 0;
  const { fetchMock } = stubFetch({
    'GET /internal/v1/careSignup?offset=0&limit=100': () => json([item]),
    'GET /internal/v1/careSignup/signup1': () =>
      ++detail === 1 ? tmfError(503, 'Service Unavailable') : json(item),
    'POST /internal/v1/careSignup/signup1/decision': () =>
      ++decisions === 1 ? tmfError(409, 'signup already decided') : json({}),
  });
  render(<Signups />);
  fireEvent.click(await screen.findByRole('button', { name: item.email }));
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('503 Service Unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toContain('409 signup already decided'),
  );
  expect(screen.getByRole('button', { name: 'Reject' })).toHaveProperty('disabled', false);
  fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
  await waitFor(() => expect(decisions).toBe(2));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  const bodies = fetchMock.mock.calls
    .filter(([, init]) => init?.method === 'POST')
    .map(([, init]) => String(init?.body));
  expect(bodies).toEqual(['{"decision":"reject"}', '{"decision":"reject"}']);
});
