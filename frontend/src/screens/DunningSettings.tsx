import { useState, type ReactNode } from 'react';
import {
  loadDunningSettings,
  saveDunningSettings,
  type DunningChange,
  type DunningSettings,
} from '../api/dunning';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate } from '../format/dates';
import { plural } from '../format/labels';
import { formatCents, parseAmount, toCents } from '../format/money';
import {
  Button,
  Dialog,
  Field,
  KeyValue,
  MoneyField,
  PageHeader,
  Panel,
  TextInput,
  submitDialogPrimary,
  useToast,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, SkeletonRows } from '../ui/states';
import { Footer, useWrite } from './FinancialForms';
import './screens.css';

// The Dunning settings tab of Settings (#386, #467; docs/decisions/dunning.md): the days after O (the
// first overdue date) of the two reminders and the suspension, and the smallest amount past
// due that counts. Field errors show at the field; the server's 422 still shows in the footer.

const DAYS = [
  { key: 'reminder1Days', label: 'First reminder' },
  { key: 'reminder2Days', label: 'Second reminder' },
  { key: 'suspendDays', label: 'Suspension' },
] as const;
type DayKey = (typeof DAYS)[number]['key'];
const MAX_DAYS = 365;

export function DunningSettingsTab({ tabs }: { tabs: ReactNode }) {
  const context = useSystemContext();
  const settings = useResource('dunning-settings', loadDunningSettings);
  const [editing, setEditing] = useState(false);
  const toast = useToast();
  const done = (message: string) => {
    setEditing(false);
    toast(message);
    settings.retry();
  };
  return (
    <div className="bss-screen" data-ui="ui.billingSettings.dunning">
      <div className="bss-title-row">
        <PageHeader title="Settings" />
        <Button
          variant="primary"
          disabled={settings.status !== 'ok'}
          onClick={() => setEditing(true)}
        >
          Edit dunning settings
        </Button>
      </div>
      {tabs}
      <Panel>
        <Note tone="info">
          Postpaid accounts owing at least the minimum past due get the reminders and then the
          suspension of their lines on these days after the first overdue date. Changes apply to
          steps not yet taken; steps taken stand.
        </Note>
        {settings.status === 'loading' && (
          <SkeletonRows
            columns={[
              { key: 'setting', label: 'Setting', width: '1fr' },
              { key: 'value', label: 'Value', width: '1fr' },
            ]}
          />
        )}
        {settings.status === 'error' && (
          <ErrorBlock
            title="Failed to load dunning settings"
            error={settings.error}
            onRetry={settings.retry}
          />
        )}
        {settings.status === 'ok' && (
          <KeyValue
            items={[
              ...DAYS.map(
                ({ key, label }) =>
                  [label, `${plural(settings.data[key], 'day')} after the first overdue date`] as [
                    string,
                    string,
                  ],
              ),
              [
                'Minimum past due',
                `${formatCents(toCents(settings.data.minimumOverdue))} ${context.currency}`,
              ],
              [
                'Last changed',
                settings.data.updatedAt === null
                  ? 'Never (defaults)'
                  : businessDate(settings.data.updatedAt, context.timeZone),
              ],
            ]}
          />
        )}
      </Panel>
      {editing && settings.status === 'ok' && (
        <DunningSettingsDialog
          settings={settings.data}
          currency={context.currency}
          onClose={() => setEditing(false)}
          onDone={done}
        />
      )}
    </div>
  );
}

type Form = Record<DayKey | 'minimumOverdue', string>;

/** The error of each field, or none: whole days in range, increasing, an exact amount. */
export function dunningFormErrors(form: Form): Partial<Record<keyof Form, string>> {
  const errors: Partial<Record<keyof Form, string>> = {};
  const value: Partial<Record<DayKey, number>> = {};
  for (const { key } of DAYS) {
    const text = form[key].trim();
    if (!/^\d+$/.test(text) || Number(text) > MAX_DAYS) {
      errors[key] = `Enter whole days from 0 to ${MAX_DAYS}.`;
    } else {
      value[key] = Number(text);
    }
  }
  const { reminder1Days: r1, reminder2Days: r2, suspendDays: s } = value;
  if (r1 !== undefined && r2 !== undefined && r2 <= r1) {
    errors.reminder2Days = 'Must be later than the first reminder.';
  }
  if (r2 !== undefined && s !== undefined && s <= r2) {
    errors.suspendDays = 'Must be later than the second reminder.';
  }
  if (parseAmount(form.minimumOverdue) === null) {
    errors.minimumOverdue = 'Enter an amount of 0 or more with at most 2 decimals.';
  }
  return errors;
}

/** Only what changed: days as integers, the minimum as its canonical decimal string. */
export function dunningChanges(form: Form, settings: DunningSettings): DunningChange {
  const change: DunningChange = {};
  for (const { key } of DAYS) {
    if (Number(form[key]) !== settings[key]) change[key] = Number(form[key]);
  }
  const minimum = parseAmount(form.minimumOverdue);
  if (minimum !== null && toCents(Number(minimum)) !== toCents(settings.minimumOverdue)) {
    change.minimumOverdue = minimum;
  }
  return change;
}

function DunningSettingsDialog({
  settings,
  currency,
  onClose,
  onDone,
}: {
  settings: DunningSettings;
  currency: string;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [form, setForm] = useState<Form>(() => ({
    reminder1Days: String(settings.reminder1Days),
    reminder2Days: String(settings.reminder2Days),
    suspendDays: String(settings.suspendDays),
    minimumOverdue: formatCents(toCents(settings.minimumOverdue)).replace(/,/g, ''),
  }));
  const write = useWrite(onDone);
  const errors = dunningFormErrors(form);
  const valid = Object.keys(errors).length === 0;
  const changes = valid ? dunningChanges(form, settings) : {};
  return (
    <Dialog
      title="Edit dunning settings"
      size="M"
      ui="ui.billingSettings.dunning.form"
      onClose={onClose}
      onSubmit={submitDialogPrimary}
      footer={
        <Footer write={write} onRefresh={() => onDone('Dunning settings refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            busyLabel="Saving…"
            disabled={!valid || Object.keys(changes).length === 0 || write.busy}
            onClick={() =>
              void write.run(async () => {
                await saveDunningSettings(changes);
                return 'Dunning settings saved';
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
          {DAYS.map(({ key, label }) => (
            <Field
              key={key}
              label={`${label} (days after the first overdue date)`}
              help={`0–${MAX_DAYS} days`}
              error={errors[key]}
            >
              <TextInput
                aria-label={label}
                aria-invalid={errors[key] !== undefined}
                type="number"
                min={0}
                max={MAX_DAYS}
                step={1}
                required
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            </Field>
          ))}
          <Field
            label="Minimum past due"
            help="A smaller amount past due starts no dunning"
            error={errors.minimumOverdue}
          >
            <MoneyField
              aria-label="Minimum past due"
              aria-invalid={errors.minimumOverdue !== undefined}
              currency={currency}
              required
              value={form.minimumOverdue}
              onChange={(e) => setForm({ ...form, minimumOverdue: e.target.value })}
            />
          </Field>
        </fieldset>
      </div>
    </Dialog>
  );
}
