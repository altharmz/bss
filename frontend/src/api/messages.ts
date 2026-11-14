import type { components as Internal } from './generated/internal';
import { getJson, getPage, patchJson, postJson } from './client';

// Customer messages and marketing consent (docs/decisions/notifications.md). The message log
// is TMF681 `communicationMessage`; consent and resend are ours under
// `/internal/v1/notification`. Nothing is decided here: the server applies consent, quiet
// hours, retries and the state rules, the UI shows what it answers.

export const MESSAGE_PAGE = 25;
export const MESSAGES = '/tmf-api/communicationManagement/v4/communicationMessage';
const CONSENT = '/internal/v1/notification/consent';
const RESEND = '/internal/v1/notification/message';

export type Consent = Internal['schemas']['NotificationConsent'];
export type ConsentEntry = Internal['schemas']['ConsentEntry'];
export type Channel = 'sms' | 'email';
export type Category = 'transactional' | 'marketing';

/** The sender of a message an operator writes from the card (TMF681 requires one). */
export const OPERATOR_SENDER = { '@type': 'Sender', name: 'bss admin' } as const;

/** Unsent states: a message in them can be cancelled, not resent. */
export const OPEN_STATES = ['initial', 'inProgress'];

interface Characteristic {
  name?: string;
  value?: unknown;
}

interface Contact {
  name?: string;
  email?: string;
  phoneNumber?: string;
}

interface MessageBody {
  id?: string;
  state?: string;
  messageType?: string;
  subject?: string;
  content?: string;
  description?: string;
  priority?: string;
  scheduledSendTime?: string;
  sendTime?: string;
  sendTimeComplete?: string;
  tryTimes?: number;
  characteristic?: Characteristic[];
  receiver?: Contact[];
  sender?: Contact;
}

/** One message as the tab and its detail show it. */
export interface Message {
  id: string;
  state: string;
  channel: Channel;
  category: string;
  event: string;
  template: string;
  stateReason: string | null;
  lastError: string | null;
  resendOf: string | null;
  subject: string | null;
  content: string;
  receiver: { name: string | null; address: string | null };
  sender: string | null;
  scheduledSendTime: string | null;
  sendTime: string | null;
  sendTimeComplete: string | null;
  tryTimes: number;
}

function trait(body: MessageBody, name: string): string | null {
  const value = body.characteristic?.find((c) => c.name === name)?.value;
  return typeof value === 'string' ? value : null;
}

export function toMessage(body: MessageBody): Message {
  if (typeof body.id !== 'string' || typeof body.state !== 'string') {
    throw new Error('the API sent a message without an id or a state');
  }
  if (body.messageType !== 'SMS' && body.messageType !== 'Email') {
    throw new Error(`the API sent a message of an unknown type ${String(body.messageType)}`);
  }
  const channel: Channel = body.messageType === 'SMS' ? 'sms' : 'email';
  const receiver = body.receiver?.[0];
  const sender = body.sender;
  return {
    id: body.id,
    state: body.state,
    channel,
    category: trait(body, 'category') ?? 'transactional',
    event: trait(body, 'event') ?? '',
    template: trait(body, 'template') ?? '',
    stateReason: trait(body, 'stateReason'),
    lastError: trait(body, 'lastError'),
    resendOf: trait(body, 'resendOf'),
    subject: body.subject ?? null,
    content: body.content ?? '',
    receiver: {
      name: receiver?.name ?? null,
      address: (channel === 'sms' ? receiver?.phoneNumber : receiver?.email) ?? null,
    },
    sender: sender?.name ?? sender?.email ?? sender?.phoneNumber ?? null,
    scheduledSendTime: body.scheduledSendTime ?? null,
    sendTime: body.sendTime ?? null,
    sendTimeComplete: body.sendTimeComplete ?? null,
    tryTimes: body.tryTimes ?? 0,
  };
}

/** One page of a customer's messages, newest first (the server's order). */
export async function loadMessages(customerId: string, offset: number, signal: AbortSignal) {
  const page = await getPage<MessageBody>(
    `${MESSAGES}?receiver.party.id=${encodeURIComponent(customerId)}&offset=${offset}&limit=${MESSAGE_PAGE}`,
    signal,
  );
  return { items: page.items.map(toMessage), more: page.more };
}

export async function loadMessage(id: string, signal: AbortSignal): Promise<Message> {
  return toMessage(await getJson<MessageBody>(`${MESSAGES}/${encodeURIComponent(id)}`, signal));
}

export interface NewMessage {
  channel: Channel;
  category: Category;
  subject: string;
  content: string;
}

/** The TMF681 POST body of a message to the customer: the server completes the address from
 * the customer's contact media, and cancels a marketing message without an opt-in. */
export function newMessageBody(customerId: string, message: NewMessage) {
  return {
    '@type': 'CommunicationMessage',
    messageType: message.channel === 'sms' ? 'SMS' : 'Email',
    ...(message.channel === 'email' ? { subject: message.subject } : {}),
    content: message.content,
    characteristic: [
      { '@type': 'StringCharacteristic', name: 'category', value: message.category },
    ],
    receiver: [{ '@type': 'Receiver', party: { id: customerId, '@referredType': 'Customer' } }],
    sender: OPERATOR_SENDER,
  };
}

export async function sendMessage(customerId: string, message: NewMessage): Promise<Message> {
  return toMessage(await postJson<MessageBody>(MESSAGES, newMessageBody(customerId, message)));
}

/** Cancel a message that has not been sent (409 once it has). */
export async function cancelMessage(id: string): Promise<Message> {
  return toMessage(
    await patchJson<MessageBody>(`${MESSAGES}/${encodeURIComponent(id)}`, {
      '@type': 'CommunicationMessage',
      state: 'cancelled',
    }),
  );
}

/** Queue a copy of a sent, failed or cancelled message; the answer is the copy. */
export async function resendMessage(id: string): Promise<Message> {
  return toMessage(await postJson<MessageBody>(`${RESEND}/${encodeURIComponent(id)}/resend`, {}));
}

export function loadConsent(customerId: string, signal: AbortSignal): Promise<Consent> {
  return getJson<Consent>(`${CONSENT}?customer.id=${encodeURIComponent(customerId)}`, signal);
}

export function consentBody(
  customerId: string,
  channel: Channel,
  optedIn: boolean,
  source: string,
) {
  return { customer: { id: customerId }, channel, optedIn, source };
}

export function recordConsent(
  customerId: string,
  channel: Channel,
  optedIn: boolean,
  source: string,
): Promise<Consent> {
  return postJson<Consent>(CONSENT, consentBody(customerId, channel, optedIn, source));
}
