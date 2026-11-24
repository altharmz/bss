import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { json, tmfError, type Handler } from '../test/fetch';
import { expectUi } from '../test/expectUi';
import { C, backend, open } from '../test/subscriberCardKit';

// The Messages tab (issue 192b): TMF681 message log, detail, Resend, Cancel, New message,
// and the customer's marketing consent. Write bodies are replayed against the real API in
// tests/internal/test_ui_requests.py (test_messages_and_consent_as_the_ui_sends_them).

const MSG = '/tmf-api/communicationManagement/v4/communicationMessage';
const LIST = `GET ${MSG}?receiver.party.id=${C}&offset=0&limit=25`;
const CONSENT = `GET /internal/v1/notification/consent?customer.id=${C}`;
const M1 = 'e0000001-0000-4000-8000-000000000001';
const M2 = 'e0000002-0000-4000-8000-000000000002';
const M3 = 'e0000003-0000-4000-8000-000000000003';
const trait = (name: string, value: string) => ({
  '@type': 'StringCharacteristic',
  name,
  valueType: 'string',
  value,
});
const message = (id: string, over: Record<string, unknown> = {}, traits: object[] = []) => ({
  id,
  href: `${MSG}/${id}`,
  '@type': 'CommunicationMessage',
  messageType: 'SMS',
  content: 'Your balance is low: 3.20 USD left.',
  state: 'completed',
  tryTimes: 1,
  sendTime: '2026-10-01T06:00:00Z',
  sendTimeComplete: '2026-10-01T06:00:01Z',
  characteristic: [
    trait('category', 'transactional'),
    trait('event', 'low_balance'),
    trait('template', 'low_balance'),
    ...traits,
  ],
  receiver: [{ '@type': 'Receiver', name: 'Olga Kuznetsova', phoneNumber: '12125550123' }],
  sender: { '@type': 'Sender', name: 'bss' },
  ...over,
});
const SENT = message(M1);
const FAILED = message(
  M2,
  {
    messageType: 'Email',
    subject: 'Card declined',
    state: 'failed',
    tryTimes: 5,
    sendTimeComplete: undefined,
    receiver: [{ '@type': 'Receiver', email: 'olga@example.org' }],
  },
  [trait('lastError', 'mailbox unavailable')],
);
const QUEUED = message(M3, {
  state: 'initial',
  tryTimes: 0,
  sendTime: undefined,
  sendTimeComplete: undefined,
  scheduledSendTime: '2026-10-01T05:00:00Z',
});
const entry = (id: string, channel: string, optedIn: boolean, at: string) => ({
  id,
  channel,
  category: 'marketing',
  optedIn,
  source: 'call 2026-10-01',
  recordedBy: 'i.petrova',
  recordedAt: at,
});
const OPTED_IN = entry('k0000001-0000-4000-8000-000000000001', 'sms', true, '2026-10-01T07:00:00Z');
const consent = (sms: object | null, email: object | null, history: object[]) => ({
  customer: { id: C, href: `/tmf-api/customer/v5/customer/${C}` },
  marketing: { sms, email },
  history,
});
const NO_CONSENT = consent(null, null, []);

function tab(routes: Record<string, Handler>) {
  return backend({ [CONSENT]: () => json(NO_CONSENT), ...routes });
}

function bodyOf(
  fetchMock: ReturnType<typeof backend>['fetchMock'],
  method: string,
  path: string,
): unknown {
  const call = fetchMock.mock.calls.find(
    ([url, init]) => init?.method === method && String(url) === path,
  );
  if (call === undefined) throw new Error(`no ${method} ${path}`);
  return JSON.parse(String(call[1]?.body));
}

describe('messages tab', () => {
  it('is loaded only when opened', async () => {
    const { calls } = backend();
    await open();
    expect(screen.getByRole('tab', { name: /^Messages/ })).toBeTruthy();
    expect(calls.some((c) => c.includes('communicationMessage') || c.includes('consent'))).toBe(
      false,
    );
  });

  it('lists channel, event, receiver, state with its reason and the error', async () => {
    const cancelled = message('e0000004-0000-4000-8000-000000000004', { state: 'cancelled' }, [
      trait('stateReason', 'noConsent'),
    ]);
    tab({ [LIST]: () => json([SENT, FAILED, QUEUED, cancelled]) });
    await open('#messages');
    const section = await waitFor(() => expectUi('ui.subscriber.messages'));
    await within(section).findByText('mailbox unavailable');
    const rows = within(section).getAllByRole('row');
    const text = rows.map((r) => r.textContent ?? '');
    expect(text.find((t) => t.includes('12125550123') && t.includes('Sent'))).toContain(
      '2026-10-01 09:00:01',
    );
    const failed = text.find((t) => t.includes('olga@example.org')) ?? '';
    expect(failed).toContain('Email');
    expect(failed).toContain('Failed');
    expect(failed).toContain('mailbox unavailable');
    expect(text.find((t) => t.includes('Queued'))).toContain('due 2026-10-01 08:00:00');
    expect(text.find((t) => t.includes('Cancelled'))).toContain('no marketing consent');
    expect(text.some((t) => t.includes('low balance'))).toBe(true);
  });

  it('pages by the Link header', async () => {
    const second = `GET ${MSG}?receiver.party.id=${C}&offset=25&limit=25`;
    tab({ [LIST]: () => json([SENT], { more: true }), [second]: () => json([FAILED]) });
    await open('#messages');
    const next = await waitFor(() => expectUi('ui.subscriber.messages.next'));
    fireEvent.click(next);
    await screen.findByText('mailbox unavailable');
    expect(screen.getByText('Showing 26–26')).toBeTruthy();
    expect((expectUi('ui.subscriber.messages.next') as HTMLButtonElement).disabled).toBe(true);
  });

  it('opens the detail and resends a failed message', async () => {
    const copy = message('e0000009-0000-4000-8000-000000000009', { state: 'initial' });
    const { fetchMock } = tab({
      [LIST]: () => json([FAILED]),
      [`GET ${MSG}/${M2}`]: () => json(FAILED),
      [`POST /internal/v1/notification/message/${M2}/resend`]: () => json(copy, { status: 201 }),
    });
    await open('#messages');
    fireEvent.click(await screen.findByText('mailbox unavailable'));
    const dialog = await waitFor(() => expectUi('ui.subscriber.messages.detail'));
    await within(dialog).findByText('Card declined');
    expect(within(dialog).getByTestId('message-content').textContent).toContain('balance is low');
    expect(within(dialog).queryByText('Cancel message')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resend' }));
    await screen.findByText('Message queued again');
    expect(bodyOf(fetchMock, 'POST', `/internal/v1/notification/message/${M2}/resend`)).toEqual({});
  });

  it('cancels a queued message with the exact merge patch', async () => {
    const { fetchMock } = tab({
      [LIST]: () => json([QUEUED]),
      [`GET ${MSG}/${M3}`]: () => json(QUEUED),
      [`PATCH ${MSG}/${M3}`]: () => json({ ...QUEUED, state: 'cancelled' }),
    });
    await open('#messages');
    fireEvent.click(await screen.findByText(/due 2026-10-01 08:00/));
    const dialog = await waitFor(() => expectUi('ui.subscriber.messages.detail'));
    expect(within(dialog).queryByRole('button', { name: 'Resend' })).toBeNull();
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Cancel message' }));
    await screen.findByText('Message cancelled');
    expect(bodyOf(fetchMock, 'PATCH', `${MSG}/${M3}`)).toEqual({
      '@type': 'CommunicationMessage',
      state: 'cancelled',
    });
  });

  it("shows the server's refusal of a resend", async () => {
    tab({
      [LIST]: () => json([SENT]),
      [`GET ${MSG}/${M1}`]: () => json(SENT),
      [`POST /internal/v1/notification/message/${M1}/resend`]: () =>
        tmfError(409, 'the message had no receiver address: nothing to send again'),
    });
    await open('#messages');
    fireEvent.click(await screen.findByText('12125550123'));
    const dialog = await waitFor(() => expectUi('ui.subscriber.messages.detail'));
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Resend' }));
    await within(dialog).findByText(/nothing to send again/);
    expect(
      (within(dialog).getByRole('button', { name: 'Resend' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('sends a new marketing email with the exact TMF681 body and reports no consent', async () => {
    const refused = message(M1, { state: 'cancelled', messageType: 'Email' }, [
      trait('stateReason', 'noConsent'),
    ]);
    const { fetchMock } = tab({
      [LIST]: () => json([]),
      [`POST ${MSG}`]: () => json(refused, { status: 201 }),
    });
    await open('#messages');
    await screen.findByText('No messages');
    fireEvent.click(expectUi('ui.subscriber.messages.newItem'));
    const dialog = await waitFor(() => expectUi('ui.subscriber.messages.new'));
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Email' }));
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Marketing' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
    expect(await within(dialog).findByText('Enter a subject')).toBeTruthy();
    expect(within(dialog).getByText('Enter the text')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Subject'), { target: { value: ' Offer ' } });
    fireEvent.change(within(dialog).getByLabelText('Text'), {
      target: { value: 'Half price this week' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
    await screen.findByText('Not sent: no marketing consent');
    expect(bodyOf(fetchMock, 'POST', MSG)).toEqual({
      '@type': 'CommunicationMessage',
      messageType: 'Email',
      subject: 'Offer',
      content: 'Half price this week',
      characteristic: [{ '@type': 'StringCharacteristic', name: 'category', value: 'marketing' }],
      receiver: [{ '@type': 'Receiver', party: { id: C, '@referredType': 'Customer' } }],
      sender: { '@type': 'Sender', name: 'bss admin' },
    });
  });

  it('sends a transactional SMS without a subject', async () => {
    const { fetchMock } = tab({
      [LIST]: () => json([]),
      [`POST ${MSG}`]: () => json(QUEUED, { status: 201 }),
    });
    await open('#messages');
    await screen.findByText('No messages');
    fireEvent.click(expectUi('ui.subscriber.messages.newItem'));
    const dialog = await waitFor(() => expectUi('ui.subscriber.messages.new'));
    fireEvent.change(within(dialog).getByLabelText('Text'), { target: { value: 'Hello' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
    await screen.findByText('Message queued');
    expect(bodyOf(fetchMock, 'POST', MSG)).toEqual({
      '@type': 'CommunicationMessage',
      messageType: 'SMS',
      content: 'Hello',
      characteristic: [
        { '@type': 'StringCharacteristic', name: 'category', value: 'transactional' },
      ],
      receiver: [{ '@type': 'Receiver', party: { id: C, '@referredType': 'Customer' } }],
      sender: { '@type': 'Sender', name: 'bss admin' },
    });
  });
});

describe('marketing consent', () => {
  it('shows the current choice per channel and the history', async () => {
    const out = entry(
      'k0000002-0000-4000-8000-000000000002',
      'email',
      false,
      '2026-09-30T07:00:00Z',
    );
    tab({
      [LIST]: () => json([]),
      [CONSENT]: () => json(consent(OPTED_IN, out, [OPTED_IN, out])),
    });
    await open('#messages');
    const sms = await waitFor(() => expectUi('ui.subscriber.consent.sms'));
    expect(sms.textContent).toContain(
      'Opted in · 2026-10-01 10:00:00 · call 2026-10-01 · by i.petrova',
    );
    expect(within(sms).getByRole('button', { name: 'Opt out' })).toBeTruthy();
    const email = expectUi('ui.subscriber.consent.email');
    expect(email.textContent).toContain('Opted out');
    expect(within(email).getByRole('button', { name: 'Opt in' })).toBeTruthy();
    expect(within(expectUi('ui.subscriber.consent')).getAllByRole('row')).toHaveLength(3); // the head and two entries
  });

  it('records an opt-in with its source and reloads', async () => {
    let recorded = false;
    const { fetchMock } = tab({
      [LIST]: () => json([]),
      [CONSENT]: () => json(recorded ? consent(OPTED_IN, null, [OPTED_IN]) : NO_CONSENT),
      'POST /internal/v1/notification/consent': () => {
        recorded = true;
        return json(consent(OPTED_IN, null, [OPTED_IN]), { status: 201 });
      },
    });
    await open('#messages');
    const sms = await waitFor(() => expectUi('ui.subscriber.consent.sms'));
    expect(sms.textContent).toContain('Not opted in');
    fireEvent.click(within(sms).getByRole('button', { name: 'Opt in' }));
    const dialog = await waitFor(() => expectUi('ui.subscriber.consent.record'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record opt-in' }));
    expect(await within(dialog).findByText('Enter the source')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Source'), {
      target: { value: ' call 2026-10-01 ' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record opt-in' }));
    await screen.findByText('Marketing SMS: opted in');
    await waitFor(() =>
      expect(expectUi('ui.subscriber.consent.sms').textContent).toContain('Opted in ·'),
    );
    expect(bodyOf(fetchMock, 'POST', '/internal/v1/notification/consent')).toEqual({
      customer: { id: C },
      channel: 'sms',
      optedIn: true,
      source: 'call 2026-10-01',
    });
  });

  it('keeps the messages when the consent fails to load', async () => {
    tab({
      [LIST]: () => json([SENT]),
      [CONSENT]: () => tmfError(503, 'Service Unavailable'),
    });
    await open('#messages');
    await screen.findByText('Failed to load the consent');
    expect(await screen.findByText('12125550123')).toBeTruthy();
  });
});
