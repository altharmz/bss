import { useState } from 'react';
import {
  createHub,
  deleteHub,
  EVENT_TYPES,
  HUB_APIS,
  loadSubscriptions,
  probe,
  type EventSubscription,
  type HubApi,
} from '../api/hubs';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { Button, Modal, PageTitle, Panel, StatusPill, TextInput, useToast } from '../ui/kit';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { Footer, useWrite } from './FinancialForms';
import { Field } from './SubscriberForms';
import './screens.css';

// Event subscriptions (hubs) and the System panel (docs/next-slices-design.md section 23).
// The list is ours (internal API); create and delete go to each API's TMF /hub. The server
// decides: its 422 for a callback it refuses is shown as it is, the entered values stay.
// Visible to every role, as the hub API is (users.md U4).

const COLUMNS: Column[] = [
  { key: 'api', label: 'API', width: '190px' },
  { key: 'callback', label: 'Callback', width: 'minmax(0, 1.4fr)' },
  { key: 'events', label: 'Events', width: 'minmax(0, 1fr)' },
  { key: 'since', label: 'Subscribed', width: '150px', mono: true },
  { key: 'last', label: 'Last delivery', width: '190px' },
  { key: 'failures', label: 'Failed', width: '70px', align: 'right', mono: true },
  { key: 'actions', label: 'Actions', width: '90px' },
];

export function IntegrationsScreen() {
  useSetCrumbs([{ label: 'Integrations' }], 'integrations');
  const { timeZone } = useSystemContext();
  const toast = useToast();
  const list = useResource('event-subscriptions', loadSubscriptions);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<EventSubscription | null>(null);
  const done = (message: string) => {
    setCreating(false);
    setDeleting(null);
    list.retry();
    toast(message);
  };
  return (
    <div className="bss-screen" data-ui="ui.integrations">
      <div className="bss-title-row">
        <PageTitle title="Integrations" />
        <span className="bss-caption">Where the TMF event notifications are sent</span>
      </div>
      <Panel
        title="Event subscriptions"
        right={
          <Button data-ui="ui.integrations.new" onClick={() => setCreating(true)}>
            New subscription
          </Button>
        }
      >
        <div role="table" aria-label="Event subscriptions" data-ui="ui.integrations.list">
          <TableHead columns={COLUMNS} />
          <ListState
            resource={list}
            columns={COLUMNS}
            errorTitle="Failed to load event subscriptions"
            empty={{
              title: 'No event subscriptions',
              text: 'A subscription sends the events of one API to a callback URL.',
            }}
            total={(data) => data.length}
            rows={(data) => data}
            onResetFilters={() => {}}
          >
            {(rows) =>
              rows.map((hub) => (
                <TableRow
                  key={hub.id}
                  columns={COLUMNS}
                  cells={{
                    api: (
                      <span className="ellipsis" title={hub.api.name}>
                        {hub.api.id} {hub.api.name}
                      </span>
                    ),
                    callback: (
                      <span className="ellipsis bss-mono" title={hub.callback}>
                        {hub.callback}
                      </span>
                    ),
                    events: (
                      <span className="ellipsis" title={hub.eventTypes.join(', ')}>
                        {hub.eventTypes.length === 0
                          ? 'All events'
                          : `${hub.eventTypes.length}: ${hub.eventTypes.join(', ')}`}
                      </span>
                    ),
                    since: `${businessDate(hub.subscribedAt, timeZone)} ${clockTime(hub.subscribedAt, timeZone)}`,
                    last:
                      hub.lastDelivery === null ? (
                        <span className="bss-text-2">None yet</span>
                      ) : (
                        <span>
                          <StatusPill
                            kind="delivery"
                            status={hub.lastDelivery.state}
                            label={hub.lastDelivery.state}
                          />{' '}
                          {businessDate(hub.lastDelivery.at, timeZone)}{' '}
                          {clockTime(hub.lastDelivery.at, timeZone)}
                        </span>
                      ),
                    failures: String(hub.failures),
                    actions: (
                      <Button
                        small
                        data-ui="ui.integrations.delete"
                        aria-label={`Delete subscription ${hub.id}`}
                        onClick={() => setDeleting(hub)}
                      >
                        Delete
                      </Button>
                    ),
                  }}
                />
              ))
            }
          </ListState>
        </div>
      </Panel>
      <SystemPanel />
      {creating && (
        <NewSubscription
          onClose={() => setCreating(false)}
          onDone={done}
          onRefresh={() => done('Refreshed')}
        />
      )}
      {deleting !== null && (
        <DeleteSubscription hub={deleting} onClose={() => setDeleting(null)} onDone={done} />
      )}
    </div>
  );
}

function NewSubscription({
  onClose,
  onDone,
  onRefresh,
}: {
  onClose: () => void;
  onDone: (message: string) => void;
  onRefresh: () => void;
}) {
  const [api, setApi] = useState<HubApi>(HUB_APIS[0]);
  const [callback, setCallback] = useState('');
  const [chosen, setChosen] = useState<string[]>([]);
  const write = useWrite(onDone);
  const events = EVENT_TYPES[api.id] ?? [];
  const toggle = (event: string) =>
    setChosen(chosen.includes(event) ? chosen.filter((e) => e !== event) : [...chosen, event]);
  return (
    <Modal
      title="New subscription"
      width={560}
      onClose={onClose}
      ui="ui.integrations.form"
      footer={
        <Footer write={write} onRefresh={onRefresh}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={write.busy || callback.trim() === ''}
            onClick={() =>
              void write.run(async () => {
                await createHub(api, callback.trim(), chosen);
                return 'Subscription created';
              })
            }
          >
            Create
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <fieldset className="bss-form bss-write-fields" disabled={write.busy}>
          <Field label="API">
            <select
              className="bss-input"
              value={api.id}
              onChange={(e) => {
                setApi(HUB_APIS.find((a) => a.id === e.target.value) ?? api);
                setChosen([]);
              }}
            >
              {HUB_APIS.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.id} {a.name} ({a.version})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Callback URL">
            <TextInput
              value={callback}
              placeholder="https://example.org/events"
              onChange={(e) => setCallback(e.target.value)}
            />
          </Field>
          <fieldset className="bss-field" data-ui="ui.integrations.events">
            <legend className="label">Event types (none chosen: every event)</legend>
            <div className="event-options">
              {events.map((event) => (
                <label className="event-option" key={event}>
                  <input
                    type="checkbox"
                    checked={chosen.includes(event)}
                    onChange={() => toggle(event)}
                  />{' '}
                  {event}
                </label>
              ))}
            </div>
          </fieldset>
        </fieldset>
      </div>
    </Modal>
  );
}

function DeleteSubscription({
  hub,
  onClose,
  onDone,
}: {
  hub: EventSubscription;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const write = useWrite(onDone);
  return (
    <Modal
      title="Delete subscription"
      width={480}
      onClose={onClose}
      ui="ui.integrations.confirmDelete"
      footer={
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            disabled={write.busy}
            onClick={() =>
              void write.run(async () => {
                await deleteHub(hub);
                return 'Subscription deleted';
              })
            }
          >
            Delete
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <p>
          Stops the {hub.api.id} events going to <span className="bss-mono">{hub.callback}</span>.
          Events not yet delivered are dropped.
        </p>
      </div>
    </Modal>
  );
}

function SystemPanel() {
  const status = useResource('system:billing-status', (signal) => probe('/billing-status', signal));
  const health = useResource('system:healthz', (signal) => probe('/healthz', signal));
  return (
    <Panel
      title="System"
      right={
        <Button
          small
          onClick={() => {
            status.retry();
            health.retry();
          }}
        >
          Refresh
        </Button>
      }
    >
      <div className="bss-modal-body" data-ui="ui.system">
        <h3>Health</h3>
        {health.status === 'loading' && <p className="bss-text-2">Loading…</p>}
        {health.status === 'error' && (
          <ErrorBlock title="Health check failed" error={health.error} onRetry={health.retry} />
        )}
        {health.status === 'ok' && (
          <p data-ui="ui.system.health">
            <StatusPill
              kind="health"
              status={health.data.ok ? 'ok' : 'error'}
              label={health.data.ok ? 'OK' : 'Error'}
            />{' '}
            {health.data.ok ? '' : `HTTP ${health.data.status} ${JSON.stringify(health.data.body)}`}
          </p>
        )}
        <h3>Billing status</h3>
        {status.status === 'loading' && <p className="bss-text-2">Loading…</p>}
        {status.status === 'error' && (
          <ErrorBlock title="Billing status failed" error={status.error} onRetry={status.retry} />
        )}
        {status.status === 'ok' && (
          <dl data-ui="ui.system.billingStatus">
            <p>
              <StatusPill
                kind="health"
                status={status.data.ok ? 'ok' : 'error'}
                label={status.data.ok ? 'OK' : `HTTP ${status.data.status}`}
              />
            </p>
            {Object.entries(status.data.body ?? {}).map(([key, value]) => (
              <div key={key}>
                <dt className="system-key">{key}: </dt>
                <dd className="system-value bss-mono">{value === null ? '—' : String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
        <p>
          <a
            className="bss-link"
            data-ui="ui.system.schema"
            href="/tmf-api/schema/BssBillingAccount.schema.json"
            target="_blank"
            rel="noreferrer"
          >
            BssBillingAccount JSON Schema
          </a>
        </p>
      </div>
    </Panel>
  );
}
