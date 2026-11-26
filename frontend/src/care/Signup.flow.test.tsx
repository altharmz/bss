import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { json, stubFetch, tmfError } from '../test/fetch';
import { Signup } from './Signup';

const STORED = 'care.signup.id';
const PLANS = 'GET /self-care/v1/signup/plans';
const plan = { id: 'plan1', name: 'Prepaid S' };
const base = {
  id: 'signup 1',
  offering: plan,
  emailVerified: false,
  state: 'started',
  payment: null,
  esim: null,
  failure: null,
  identity: null,
};

function body(fetchMock: ReturnType<typeof stubFetch>['fetchMock'], url: string) {
  const call = fetchMock.mock.calls.find(([path]) => path === url);
  return JSON.parse(String(call?.[1]?.body));
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

afterEach(() => sessionStorage.removeItem(STORED));

describe('self-care signup', () => {
  it('says why the plans could not load and loads them on Retry', async () => {
    let attempts = 0;
    stubFetch({
      [PLANS]: () => (++attempts === 1 ? tmfError(503, 'Catalog unavailable') : json([plan])),
    });
    render(<Signup back={() => {}} />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Error: Catalog unavailable');
    expect(
      (screen.getByRole('button', { name: 'Send email code' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('option', { name: 'Prepaid S' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says so when no plan is on sale', async () => {
    stubFetch({ [PLANS]: () => json([]) });
    render(<Signup back={() => {}} />);
    expect(await screen.findByText('No plans are currently available.')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Send email code' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  const path = '/self-care/v1/signup/signup%201';
  const passed = {
    ...base,
    emailVerified: true,
    state: 'identityPassed',
    payment: { state: 'declined', declineReason: 'Your last card was declined.' },
  };

  it('takes a new signup from the email to a passed identity check', async () => {
    const { fetchMock } = stubFetch({
      [PLANS]: () => json([plan]),
      'POST /self-care/v1/signup': () => json(base, { status: 201 }),
      [`POST ${path}/verify`]: () => json({ ...base, emailVerified: true }),
      [`PUT ${path}/details`]: () => json(passed),
    });
    render(<Signup back={() => {}} />);
    fireEvent.change(await screen.findByLabelText('Plan'), { target: { value: 'plan1' } });
    fill('Email', 'new@example.test');
    fireEvent.click(screen.getByRole('button', { name: 'Send email code' }));

    await screen.findByLabelText('Email code');
    // the started signup survives a reload
    expect(sessionStorage.getItem(STORED)).toBe('signup 1');
    fill('Email code', '123456');
    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));

    await screen.findByLabelText('First name');
    fill('First name', 'Ana');
    fill('Last name', 'Diaz');
    fill('Date of birth', '1990-04-01');
    fill('Street address', '1 Main St');
    fill('City', 'Austin');
    fill('State (two letters)', 'TX');
    fill('ZIP code', '73301');
    fireEvent.click(screen.getByRole('button', { name: 'Verify identity' }));

    expect(await screen.findByText('Plan · Prepaid S')).toBeTruthy();
    expect(body(fetchMock, '/self-care/v1/signup')).toEqual({
      email: 'new@example.test',
      offeringId: 'plan1',
    });
    expect(body(fetchMock, `${path}/verify`)).toEqual({ code: '123456' });
    expect(body(fetchMock, `${path}/details`)).toEqual({
      givenName: 'Ana',
      familyName: 'Diaz',
      birthDate: '1990-04-01',
      serviceAddress: {
        street1: '1 Main St',
        city: 'Austin',
        stateOrProvince: 'TX',
        postCode: '73301',
      },
    });
  });

  it('pays with the gateway token, shows the eSIM and forgets the signup on the way out', async () => {
    sessionStorage.setItem(STORED, 'signup 1');
    const back = vi.fn();
    const { fetchMock } = stubFetch({
      [`GET ${path}`]: () => json(passed),
      [`POST ${path}/payment`]: () =>
        json({
          ...base,
          emailVerified: true,
          state: 'active',
          esim: {
            iccid: null,
            activationCode: 'LPA:1$sm-dp.example$ABC',
            qrPayload: 'LPA:1$sm-dp.example$ABC',
          },
        }),
    });
    render(<Signup back={back} />);
    expect(await screen.findByText('Plan · Prepaid S')).toBeTruthy();
    expect(screen.getByText('Your last card was declined.')).toBeTruthy();
    const pay = screen.getByRole('button', { name: 'Pay and activate' }) as HTMLButtonElement;
    expect(pay.disabled).toBe(true);
    const gateway = screen.getByTitle('Test payment gateway') as HTMLIFrameElement;
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'stub-card-token', token: 'tok_ok' },
        origin: location.origin,
        source: gateway.contentWindow,
      }),
    );
    await waitFor(() => expect(pay.disabled).toBe(false));
    fill('Create password', 'a long password');
    fireEvent.click(pay);

    expect(await screen.findByRole('heading', { name: 'Your line is active' })).toBeTruthy();
    expect(screen.getByText('LPA:1$sm-dp.example$ABC')).toBeTruthy();
    expect(body(fetchMock, `${path}/payment`)).toEqual({
      token: 'tok_ok',
      password: 'a long password',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Back to sign in' }));
    expect(back).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem(STORED)).toBeNull();
  });

  it('offers Retry when a stored signup cannot be read, and Back forgets it', async () => {
    sessionStorage.setItem(STORED, 'gone');
    const back = vi.fn();
    stubFetch({ 'GET /self-care/v1/signup/gone': () => tmfError(404, 'Signup not found') });
    render(<Signup back={back} />);
    expect(screen.getByText('Opening signup…')).toBeTruthy();
    expect((await screen.findByRole('alert')).textContent).toContain('Error: Signup not found');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Back to sign in' }));
    expect(back).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem(STORED)).toBeNull();
  });

  it('keeps an unfinished signup stored when the customer goes back', async () => {
    sessionStorage.setItem(STORED, 'signup 1');
    const back = vi.fn();
    stubFetch({
      'GET /self-care/v1/signup/signup%201': () => json({ ...base, emailVerified: true }),
    });
    render(<Signup back={back} />);
    await screen.findByLabelText('First name');
    fireEvent.click(screen.getByRole('button', { name: 'Back to sign in' }));
    expect(back).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem(STORED)).toBe('signup 1');
  });
});
