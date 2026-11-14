import { useState } from 'react';
import { explain } from '../api/client';
import {
  cancelMessage,
  loadConsent,
  loadMessage,
  loadMessages,
  MESSAGE_PAGE,
  OPEN_STATES,
  recordConsent,
  resendMessage,
  sendMessage,
  type Category,
  type Channel,
  type ConsentEntry,
  type Message,
} from '../api/messages';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { formatInt } from '../format/money';
import {
  Button,
  Dialog,
  Field,
  KeyValue,
  Panel,
  Segmented,
  StatusPill,
  Tag,
  TextInput,
  submitDialogPrimary,
  useToast,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, ListState, SkeletonRows } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';

// The Messages tab of the subscriber card (docs/decisions/notifications.md, issue 192b): the
// customer's marketing consent per channel with its history, and the TMF681 message log with
// a detail, Resend, Cancel and New message. The server decides everything (consent, quiet
// hours, retries, what may be resent or cancelled); the UI offers an action only in the
// state the server accepts it in, and still shows the server's refusal.

const CHANNEL: Record<Channel, string> = { sms: 'SMS', email: 'Email' };
const REASON: Record<string, string> = {
  noConsent: 'no marketing consent',
  noRecipient: 'no address',
  operator: 'cancelled by an operator',
};
const MESSAGE_COLUMNS: Column[] = [
  { key: 'time', label: 'Time', width: '180px', mono: true },
  { key: 'channel', label: 'Channel', width: '80px' },
  { key: 'event', label: 'Event', width: '1fr' },
  { key: 'to', label: 'To', width: '180px', mono: true },
  { key: 'state', label: 'State', width: '170px' },
  { key: 'error', label: 'Error', width: '1fr' },
];
const HISTORY_COLUMNS: Column[] = [
  { key: 'time', label: 'Recorded', width: '180px', mono: true },
  { key: 'channel', label: 'Channel', width: '80px' },
  { key: 'choice', label: 'Choice', width: '110px' },
  { key: 'source', label: 'Source', width: '1fr' },
  { key: 'by', label: 'By', width: '140px' },
];

function at(value: string, timeZone: string): string {
  return `${businessDate(value, timeZone)} ${clockTime(value, timeZone)}`;
}

/** The time a row shows: sent, last tried, or when it is due; a queued message has none. */
export function messageTime(message: Message, timeZone: string): string {
  if (message.sendTimeComplete) return `${at(message.sendTimeComplete, timeZone)}`;
  if (message.sendTime) return `tried ${at(message.sendTime, timeZone)}`;
  if (message.scheduledSendTime) return `due ${at(message.scheduledSendTime, timeZone)}`;
  return '—';
}

function eventLabel(message: Message): string {
  return message.event === 'operator' ? 'Operator message' : message.event.replaceAll('_', ' ');
}

function StateCell({ message }: { message: Message }) {
  return (
    <span>
      <StatusPill kind="message" status={message.state} />
      {message.stateReason && (
        <span className="bss-secondary"> {REASON[message.stateReason] ?? message.stateReason}</span>
      )}
    </span>
  );
}

/** A write's refusal (4xx) is shown as the server's reason; anything else may have happened,
 * so the dialog stops offering the write and asks the operator to check first. */
function useWrite<T>(check: string) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  async function run(write: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setProblem('');
    try {
      return await write();
    } catch (error) {
      const status = (error as { status?: number }).status;
      const known = typeof status === 'number' && status >= 400 && status < 500;
      setProblem(known ? explain(error) : `${explain(error)}. ${check}`);
      setUncertain(!known);
      return null;
    } finally {
      setBusy(false);
    }
  }
  return { busy, problem, uncertain, run };
}

// --- consent ---

function ConsentDialog({
  customerId,
  channel,
  optedIn,
  onClose,
  onDone,
}: {
  customerId: string;
  channel: Channel;
  optedIn: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [source, setSource] = useState('');
  const [touched, setTouched] = useState(false);
  const write = useWrite('Refresh the consent before recording it again.');
  const trimmed = source.trim();
  const invalid = trimmed.length === 0 || trimmed.length > 200;
  const title = `${optedIn ? 'Opt in to' : 'Opt out of'} marketing ${CHANNEL[channel]}`;
  async function save() {
    setTouched(true);
    if (invalid) return;
    const done = await write.run(() => recordConsent(customerId, channel, optedIn, trimmed));
    if (done !== null) {
      toast(`Marketing ${CHANNEL[channel]}: ${optedIn ? 'opted in' : 'opted out'}`);
      onDone();
    }
  }
  return (
    <Dialog
      title={title}
      size="S"
      onClose={onClose}
      onSubmit={submitDialogPrimary}
      ui="ui.subscriber.consent.record"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={write.busy || write.uncertain}
            onClick={() => void save()}
          >
            {optedIn ? 'Record opt-in' : 'Record opt-out'}
          </Button>
        </>
      }
    >
      <p>
        {optedIn
          ? `Marketing ${CHANNEL[channel]} messages may be sent to this customer from now on.`
          : `No marketing ${CHANNEL[channel]} message is sent to this customer from now on; transactional messages still are.`}
      </p>
      <Field
        label="Source"
        help="Where the customer gave this choice, e.g. a call, the signup form (up to 200 characters)."
        error={
          touched && trimmed.length === 0
            ? 'Enter the source'
            : trimmed.length > 200
              ? 'At most 200 characters'
              : undefined
        }
      >
        <TextInput aria-label="Source" value={source} onChange={(e) => setSource(e.target.value)} />
      </Field>
      {write.problem && <Note tone="negative">{write.problem}</Note>}
    </Dialog>
  );
}

function ConsentPanel({ customerId }: { customerId: string }) {
  const { timeZone } = useSystemContext();
  const [version, setVersion] = useState(0);
  const consent = useResource(`consent:${customerId}:${version}`, (signal) =>
    loadConsent(customerId, signal),
  );
  const [recording, setRecording] = useState<{ channel: Channel; optedIn: boolean } | null>(null);
  const describe = (entry: ConsentEntry | null | undefined) =>
    entry === null || entry === undefined
      ? 'Not opted in (no choice recorded)'
      : `${entry.optedIn ? 'Opted in' : 'Opted out'} · ${at(entry.recordedAt, timeZone)} · ${entry.source} · by ${entry.recordedBy}`;
  return (
    <Panel
      title="Marketing consent"
      subtitle="The customer's choice for all its accounts; transactional messages need none"
    >
      <div data-ui="ui.subscriber.consent">
        {consent.status === 'loading' && <SkeletonRows columns={HISTORY_COLUMNS} rows={2} />}
        {consent.status === 'error' && (
          <ErrorBlock
            title="Failed to load the consent"
            error={consent.error}
            onRetry={consent.retry}
          />
        )}
        {consent.status === 'ok' && (
          <>
            {(['sms', 'email'] as const).map((channel) => {
              const current = consent.data.marketing[channel];
              const optedIn = current?.optedIn === true;
              return (
                <div
                  key={channel}
                  className="consent-row"
                  data-ui={`ui.subscriber.consent.${channel}`}
                >
                  <strong>{CHANNEL[channel]}</strong>
                  <span>{describe(current)}</span>
                  <Button small onClick={() => setRecording({ channel, optedIn: !optedIn })}>
                    {optedIn ? 'Opt out' : 'Opt in'}
                  </Button>
                </div>
              );
            })}
            <h4 className="consent-history-title">History</h4>
            <TableHead columns={HISTORY_COLUMNS} />
            {consent.data.history.length === 0 ? (
              <p className="bss-secondary">No choice recorded yet.</p>
            ) : (
              consent.data.history.map((entry) => (
                <TableRow
                  key={entry.id}
                  columns={HISTORY_COLUMNS}
                  cells={{
                    time: at(entry.recordedAt, timeZone),
                    channel: CHANNEL[entry.channel],
                    choice: entry.optedIn ? 'Opted in' : 'Opted out',
                    source: entry.source,
                    by: entry.recordedBy,
                  }}
                />
              ))
            )}
          </>
        )}
      </div>
      {recording !== null && (
        <ConsentDialog
          customerId={customerId}
          channel={recording.channel}
          optedIn={recording.optedIn}
          onClose={() => setRecording(null)}
          onDone={() => {
            setRecording(null);
            setVersion((v) => v + 1);
          }}
        />
      )}
    </Panel>
  );
}

// --- messages ---

function MessageDialog({
  id,
  onClose,
  onChanged,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { timeZone } = useSystemContext();
  const message = useResource(`message:${id}`, (signal) => loadMessage(id, signal));
  const write = useWrite<Message>('Refresh the message list before trying again.');
  async function act(kind: 'resend' | 'cancel') {
    const done = await write.run(() => (kind === 'resend' ? resendMessage(id) : cancelMessage(id)));
    if (done !== null) {
      toast(kind === 'resend' ? 'Message queued again' : 'Message cancelled');
      onChanged();
    }
  }
  const loaded = message.status === 'ok' ? message.data : null;
  const open = loaded !== null && OPEN_STATES.includes(loaded.state);
  return (
    <Dialog
      title="Message"
      size="M"
      onClose={onClose}
      ui="ui.subscriber.messages.detail"
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          {loaded !== null && open && (
            <Button
              data-ui="ui.subscriber.messages.cancel"
              disabled={write.busy || write.uncertain}
              onClick={() => void act('cancel')}
            >
              Cancel message
            </Button>
          )}
          {loaded !== null && !open && (
            <Button
              data-ui="ui.subscriber.messages.resend"
              disabled={write.busy || write.uncertain || loaded.receiver.address === null}
              onClick={() => void act('resend')}
            >
              Resend
            </Button>
          )}
        </>
      }
    >
      {message.status === 'loading' && <p className="bss-secondary">Loading the message…</p>}
      {message.status === 'error' && (
        <ErrorBlock
          title="Failed to load the message"
          error={message.error}
          onRetry={message.retry}
        />
      )}
      {loaded !== null && (
        <>
          <KeyValue
            items={[
              ['State', <StateCell key="state" message={loaded} />],
              ['Channel', CHANNEL[loaded.channel]],
              ['Event', `${eventLabel(loaded)} (template ${loaded.template})`],
              ['Category', loaded.category === 'marketing' ? 'Marketing' : 'Transactional'],
              [
                'To',
                [loaded.receiver.name, loaded.receiver.address ?? 'no address']
                  .filter(Boolean)
                  .join(' · '),
              ],
              ['From', loaded.sender ?? '—'],
              ['Time', messageTime(loaded, timeZone)],
              ['Attempts', formatInt(loaded.tryTimes)],
              ...(loaded.lastError ? [['Last error', loaded.lastError] as [string, string]] : []),
              ...(loaded.resendOf ? [['Resend of', loaded.resendOf] as [string, string]] : []),
            ]}
          />
          {loaded.subject && (
            <p>
              <strong>{loaded.subject}</strong>
            </p>
          )}
          <pre className="message-content" data-testid="message-content">
            {loaded.content}
          </pre>
          {!open && loaded.receiver.address === null && (
            <Note tone="info">The message had no address, so it cannot be sent again.</Note>
          )}
          {!open && loaded.receiver.address !== null && (
            <Note tone="info">
              Resend queues a copy; consent and the SMS quiet hours apply to it again.
            </Note>
          )}
        </>
      )}
      {write.problem && <Note tone="negative">{write.problem}</Note>}
    </Dialog>
  );
}

function NewMessageDialog({
  customerId,
  customerName,
  onClose,
  onDone,
}: {
  customerId: string;
  customerName: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [channel, setChannel] = useState<Channel>('sms');
  const [category, setCategory] = useState<Category>('transactional');
  const [subject, setSubject] = useState('');
  const [content, setContent] = useState('');
  const [touched, setTouched] = useState(false);
  const write = useWrite<Message>('Check the message list before sending again.');
  const needsSubject = channel === 'email' && subject.trim() === '';
  const needsContent = content.trim() === '';
  async function send() {
    setTouched(true);
    if (needsSubject || needsContent) return;
    const sent = await write.run(() =>
      sendMessage(customerId, {
        channel,
        category,
        subject: subject.trim(),
        content: content.trim(),
      }),
    );
    if (sent === null) return;
    toast(
      sent.state === 'cancelled'
        ? `Not sent: ${REASON[sent.stateReason ?? ''] ?? sent.stateReason ?? 'cancelled'}`
        : 'Message queued',
    );
    onDone();
  }
  return (
    <Dialog
      title="New message"
      context={customerName}
      size="M"
      onClose={onClose}
      ui="ui.subscriber.messages.new"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={write.busy || write.uncertain}
            onClick={() => void send()}
          >
            Send
          </Button>
        </>
      }
    >
      <Field label="Channel">
        <Segmented
          as="radio"
          label="Channel"
          value={channel}
          onChange={setChannel}
          options={[
            { key: 'sms', label: 'SMS' },
            { key: 'email', label: 'Email' },
          ]}
        />
      </Field>
      <Field
        label="Category"
        help="Marketing is sent only with the customer's opt-in for the channel."
      >
        <Segmented
          as="radio"
          label="Category"
          value={category}
          onChange={setCategory}
          options={[
            { key: 'transactional', label: 'Transactional' },
            { key: 'marketing', label: 'Marketing' },
          ]}
        />
      </Field>
      {channel === 'email' && (
        <Field label="Subject" error={touched && needsSubject ? 'Enter a subject' : undefined}>
          <TextInput
            aria-label="Subject"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </Field>
      )}
      <Field label="Text" error={touched && needsContent ? 'Enter the text' : undefined}>
        <textarea
          className="bss-input message-text"
          aria-label="Text"
          rows={5}
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </Field>
      <Note tone="info">
        Sent to the customer&apos;s {channel === 'sms' ? 'phone' : 'email'} contact. An SMS inside
        the quiet hours waits for their end.
      </Note>
      {write.problem && <Note tone="negative">{write.problem}</Note>}
    </Dialog>
  );
}

function MessagesPanel({ customerId, customerName }: { customerId: string; customerName: string }) {
  const { timeZone } = useSystemContext();
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [opened, setOpened] = useState<string | null>(null);
  const [writing, setWriting] = useState(false);
  const messages = useResource(`messages:${customerId}:${offset}:${version}`, (signal) =>
    loadMessages(customerId, offset, signal),
  );
  const reload = () => {
    setOffset(0);
    setVersion((v) => v + 1);
  };
  return (
    <Panel
      title="Messages"
      subtitle="SMS and email to the customer, newest first"
      right={
        <Button small data-ui="ui.subscriber.messages.newItem" onClick={() => setWriting(true)}>
          New message
        </Button>
      }
    >
      <TableHead columns={MESSAGE_COLUMNS} />
      <ListState
        resource={messages}
        columns={MESSAGE_COLUMNS}
        errorTitle="Failed to load messages"
        empty={{ title: 'No messages', text: 'Nothing has been sent to this customer.' }}
        count={(data) => data.items.length}
        rows={(data) => data.items}
        onResetFilters={() => {}}
      >
        {(rows: Message[]) => (
          <>
            {rows.map((m) => (
              <TableRow
                key={m.id}
                columns={MESSAGE_COLUMNS}
                onClick={() => setOpened(m.id)}
                cells={{
                  time: messageTime(m, timeZone),
                  channel: CHANNEL[m.channel],
                  event: (
                    <span>
                      {eventLabel(m)} {m.category === 'marketing' && <Tag>Marketing</Tag>}
                    </span>
                  ),
                  to: m.receiver.address ?? <span className="bss-dash">—</span>,
                  state: <StateCell message={m} />,
                  error: m.lastError ?? <span className="bss-dash">—</span>,
                }}
              />
            ))}
            {messages.status === 'ok' && (
              <TableFooter
                left={`Showing ${formatInt(offset + 1)}–${formatInt(offset + rows.length)}`}
                right={
                  <span className="bss-table-pager">
                    <Button
                      small
                      data-ui="ui.subscriber.messages.previous"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - MESSAGE_PAGE))}
                    >
                      Previous
                    </Button>
                    <Button
                      small
                      data-ui="ui.subscriber.messages.next"
                      disabled={!messages.data.more}
                      onClick={() => setOffset(offset + MESSAGE_PAGE)}
                    >
                      Next
                    </Button>
                  </span>
                }
              />
            )}
          </>
        )}
      </ListState>
      {opened !== null && (
        <MessageDialog
          id={opened}
          onClose={() => setOpened(null)}
          onChanged={() => {
            setOpened(null);
            reload();
          }}
        />
      )}
      {writing && (
        <NewMessageDialog
          customerId={customerId}
          customerName={customerName}
          onClose={() => setWriting(false)}
          onDone={() => {
            setWriting(false);
            reload();
          }}
        />
      )}
    </Panel>
  );
}

/** The tab body. Mounted only while shown, so the card makes no message request for a
 * subscriber whose Messages tab is never opened. */
export function MessagesTab({
  customerId,
  customerName,
}: {
  customerId: string;
  customerName: string;
}) {
  return (
    <section data-ui="ui.subscriber.messages">
      <ConsentPanel customerId={customerId} />
      <MessagesPanel customerId={customerId} customerName={customerName} />
    </section>
  );
}
