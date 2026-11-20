import { useState } from 'react';
import {
  loadLifecycleSettings,
  saveLifecycleSettings,
  type LifecycleSettings,
} from '../api/lifecycle';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import {
  Button,
  Dialog,
  KeyValue,
  PageHeader,
  Panel,
  TextInput,
  submitDialogPrimary,
  useToast,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { Footer, useWrite } from './FinancialForms';
import { Field } from './SubscriberForms';
import './screens.css';

const DURATIONS = [
  { key: 'graceDays', label: 'Grace days', max: 365 },
  { key: 'suspensionDays', label: 'Suspension days', max: 3650 },
  { key: 'numberAgingDays', label: 'Number aging days', max: 3650 },
  { key: 'paygZeroBalanceDays', label: 'Pay-as-you-go zero-balance days', max: 365 },
] as const;

// Prepaid lifecycle (docs/ui.md): the grace, suspension, number aging and pay-as-you-go
// durations. Its own screen under Prepaid since #414 (was a Billing settings tab); the
// data-ui marker keeps its old name.

export function LifecycleSettingsScreen() {
  useSetCrumbs([{ label: 'Prepaid lifecycle' }], 'prepaidLifecycle');
  const settings = useResource('lifecycle-settings', loadLifecycleSettings);
  const [editing, setEditing] = useState(false);
  const toast = useToast();
  const done = (message: string) => {
    setEditing(false);
    toast(message);
    settings.retry();
  };
  return (
    <div className="bss-screen" data-ui="ui.billingSettings.lifecycle">
      <div className="bss-title-row">
        <PageHeader title="Prepaid lifecycle" />
        <Button
          variant="primary"
          disabled={settings.status !== 'ok'}
          onClick={() => setEditing(true)}
        >
          Edit lifecycle settings
        </Button>
      </div>
      <Panel>
        <Note tone="info">
          Unfunded lines enter grace, then network suspension, then deactivation. Changes apply to
          transitions not yet taken; completed transitions are kept. Number aging is fixed when a
          line is deactivated.
        </Note>
        {settings.status === 'loading' && (
          <SkeletonRows
            columns={[
              { key: 'setting', label: 'Setting', width: '1fr' },
              { key: 'value', label: 'Days', width: '1fr' },
            ]}
          />
        )}
        {settings.status === 'error' && (
          <ErrorBlock
            title="Failed to load lifecycle settings"
            error={settings.error}
            onRetry={settings.retry}
          />
        )}
        {settings.status === 'ok' && (
          <KeyValue
            items={DURATIONS.map(({ key, label }) => [label, `${settings.data[key]} days`])}
          />
        )}
      </Panel>
      {editing && settings.status === 'ok' && (
        <LifecycleSettingsDialog
          settings={settings.data}
          onClose={() => setEditing(false)}
          onDone={done}
        />
      )}
    </div>
  );
}

function LifecycleSettingsDialog({
  settings,
  onClose,
  onDone,
}: {
  settings: LifecycleSettings;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [form, setForm] = useState(() =>
    Object.fromEntries(DURATIONS.map(({ key }) => [key, String(settings[key])])),
  );
  const write = useWrite(onDone);
  const valid = DURATIONS.every(
    ({ key, max }) => /^\d+$/.test(form[key] ?? '') && Number(form[key]) <= max,
  );
  const changes = Object.fromEntries(
    DURATIONS.filter(({ key }) => Number(form[key]) !== settings[key]).map(({ key }) => [
      key,
      Number(form[key]),
    ]),
  );
  return (
    <Dialog
      title="Edit lifecycle settings"
      size="M"
      ui="ui.billingSettings.lifecycle.form"
      onClose={onClose}
      onSubmit={submitDialogPrimary}
      footer={
        <Footer write={write} onRefresh={() => onDone('Lifecycle settings refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busyLabel="Saving…"
            disabled={!valid || Object.keys(changes).length === 0 || write.busy}
            onClick={() =>
              void write.run(async () => {
                await saveLifecycleSettings(changes);
                return 'Lifecycle settings saved';
              })
            }
          >
            Save
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <fieldset className="bss-form bss-write-fields" disabled={write.busy || write.unsure}>
          {DURATIONS.map(({ key, label, max }) => (
            <Field key={key} label={label}>
              <TextInput
                aria-label={label}
                type="number"
                min={0}
                max={max}
                step={1}
                required
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
              <span className="caption">0–{max} days</span>
            </Field>
          ))}
        </fieldset>
      </div>
    </Dialog>
  );
}
