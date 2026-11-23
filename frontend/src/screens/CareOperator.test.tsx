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
