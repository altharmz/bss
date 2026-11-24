import { submitDialogPrimary } from '../ui/kit';
import { useState } from 'react';
import {
  createBody,
  createSetting,
  deleteSetting,
  FORMAT_NAMES,
  formOf,
  loadSettings,
  patchBody,
  patchSetting,
  type Setting,
  type SettingForm,
  type SettingKind,
} from '../api/billing';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { formatInt } from '../format/money';
import {
  SelectField,
  Button,
  Dialog,
  PageHeader,
  Panel,
  Tag,
  Segmented,
  TextInput,
  useToast,
} from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import { Footer, useWrite } from './FinancialForms';
import { Field } from './SubscriberForms';
import { DunningSettingsTab } from './DunningSettings';
import './screens.css';

// Billing settings (docs/ui.md): the cycle specifications, bill
// formats and presentation media a billing account may name in its billStructure. The
// prepaid lifecycle has its own screen under Prepaid (#414); the Dunning tab (#386) holds the
// postpaid dunning steps (screens/DunningSettings.tsx). The
// subscriber Edit form selects them. The server decides: its 409/422 text is shown in the
// dialog and the entered values stay.

interface Tab {
  title: string;
  word: string; // singular, lower case
  plural: string;
  empty: string;
}

const TABS: Record<SettingKind, Tab> = {
  cycles: {
    title: 'Cycle specifications',
    word: 'cycle specification',
    plural: 'cycle specifications',
    empty: 'Create one, then select it in the subscriber Edit form.',
  },
  formats: {
    title: 'Bill formats',
    word: 'bill format',
    plural: 'bill formats',
    empty: 'Create one, then select it in the subscriber Edit form.',
  },
  media: {
    title: 'Presentation media',
    word: 'presentation medium',
    plural: 'presentation media',
    empty: 'Create one, then select it in the subscriber Edit form.',
  },
};

const COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'description', label: 'Description', width: '2fr' },
  { key: 'actions', label: '', width: '150px', align: 'right' },
];

export function BillingSettingsScreen() {
  useSetCrumbs([{ label: 'Billing settings' }], 'billingSettings');
  const [tab, setTab] = useState<SettingKind | 'dunning'>('cycles');
  const tabs = (
    <div className="bss-toolbar">
      <Segmented
        label="Billing settings sections"
        value={tab}
        onChange={setTab}
        options={[
          ...(Object.keys(TABS) as SettingKind[]).map((key) => ({
            key,
            label: TABS[key].title,
          })),
          { key: 'dunning' as const, label: 'Dunning' },
        ]}
      />
    </div>
  );
  if (tab === 'dunning') return <DunningSettingsTab tabs={tabs} />;
  return <SettingsTab key={tab} kind={tab} tabs={tabs} />;
}

function SettingsTab({ kind, tabs }: { kind: SettingKind; tabs: React.ReactNode }) {
  const tab = TABS[kind];
  const marker = `ui.billingSettings.${kind}`;
  const rows = useResource(`billing-settings:${kind}`, (signal) => loadSettings(kind, signal));
  // no "used by" count (#312/#314: it read every billing account): the server refuses
  // to delete a setting a billing account names, and the dialog shows its 409
  const toast = useToast();
  const [dialog, setDialog] = useState<
    { kind: 'new' } | { kind: 'edit' | 'delete'; setting: Setting } | null
  >(null);
  const done = (message: string) => {
    setDialog(null);
    toast(message);
    rows.retry();
  };

  return (
    <div className="bss-screen" data-ui={marker}>
      <div className="bss-title-row">
        <PageHeader
          title="Billing settings"
          count={rows.status === 'ok' ? formatInt(rows.data.length) : undefined}
        />
        <Button
          variant="primary"
          data-ui={`${marker}.new`}
          onClick={() => setDialog({ kind: 'new' })}
        >
          + New {tab.word}
        </Button>
      </div>
      {tabs}
      <Panel>
        <TableHead columns={COLUMNS} />
        <ListState
          resource={rows}
          columns={COLUMNS}
          errorTitle={`Failed to load ${tab.plural}`}
          empty={{ title: `No ${tab.plural} yet`, text: tab.empty }}
          count={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {}}
        >
          {(list) => (
            <>
              {list.map((setting) => {
                return (
                  <TableRow
                    key={setting.id}
                    columns={COLUMNS}
                    cells={{
                      name: (
                        <div className="ellipsis">
                          <span className="bss-name">{setting.name}</span>
                          {setting.builtIn && (
                            <>
                              {' '}
                              <Tag>built in</Tag>
                            </>
                          )}
                        </div>
                      ),
                      description: setting.description ?? <span className="bss-zero">—</span>,
                      actions: setting.builtIn ? (
                        <span className="bss-secondary">read-only</span>
                      ) : (
                        <span>
                          <Button
                            small
                            data-ui={`${marker}.edit`}
                            aria-label={`Edit ${setting.name}`}
                            onClick={() => setDialog({ kind: 'edit', setting })}
                          >
                            Edit
                          </Button>{' '}
                          <Button
                            small
                            data-ui={`${marker}.delete`}
                            variant="quiet"
                            tone="negative"
                            aria-label={`Delete ${setting.name}`}
                            onClick={() => setDialog({ kind: 'delete', setting })}
                          >
                            Delete
                          </Button>
                        </span>
                      ),
                    }}
                  />
                );
              })}
              <TableFooter
                left={`${formatInt(list.length)} ${tab.plural}`}
                right={
                  kind === 'cycles'
                    ? 'Stored ones are names only: billing is always monthly'
                    : 'Used by: billing accounts naming it in their bill structure'
                }
              />
            </>
          )}
        </ListState>
      </Panel>
      {(dialog?.kind === 'new' || dialog?.kind === 'edit') && (
        <SettingFormModal
          kind={kind}
          setting={dialog.kind === 'edit' ? dialog.setting : null}
          onClose={() => setDialog(null)}
          onDone={done}
        />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteSettingModal
          kind={kind}
          setting={dialog.setting}
          onClose={() => setDialog(null)}
          onDone={done}
        />
      )}
    </div>
  );
}

function SettingFormModal({
  kind,
  setting,
  onClose,
  onDone,
}: {
  kind: SettingKind;
  setting: Setting | null;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const tab = TABS[kind];
  const [form, setForm] = useState<SettingForm>(formOf(setting));
  const write = useWrite(onDone);
  const set = (change: Partial<SettingForm>) => setForm({ ...form, ...change });
  const body = setting ? patchBody(kind, setting, form) : createBody(kind, form);
  const valid = form.name.trim() !== '';
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={setting ? `Edit ${setting.name}` : `New ${tab.word}`}
      size="M"
      onClose={onClose}
      ui={`ui.billingSettings.${kind}.form`}
      footer={
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!valid || body === null || write.busy}
            onClick={() =>
              body !== null &&
              void write.run(async () => {
                if (setting) {
                  await patchSetting(kind, setting.id, body);
                  return `${form.name.trim()} saved`;
                }
                await createSetting(kind, body);
                return `${form.name.trim()} created`;
              })
            }
          >
            {setting ? 'Save' : 'Create'}
          </Button>
        </Footer>
      }
    >
      <div className="bss-modal-body">
        <fieldset className="bss-form bss-write-fields" disabled={write.busy}>
          <Field label="Name">
            {kind === 'formats' ? (
              <SelectField value={form.name} onChange={(e) => set({ name: e.target.value })}>
                {form.name === '' && <option value="">Choose a format</option>}
                {form.name !== '' && !(FORMAT_NAMES as readonly string[]).includes(form.name) && (
                  <option value={form.name}>{form.name}</option>
                )}
                {FORMAT_NAMES.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </SelectField>
            ) : (
              <TextInput value={form.name} onChange={(e) => set({ name: e.target.value })} />
            )}
          </Field>
          <Field label="Description">
            <TextInput
              value={form.description}
              onChange={(e) => set({ description: e.target.value })}
            />
          </Field>
          {kind === 'cycles' && (
            <div className="caption">
              The policy is fixed: monthly periods, no date shift. A stored specification is a name
              for it; the server refuses other values.
            </div>
          )}
          {kind === 'formats' && (
            <div className="caption">The server stores only these two bill formats.</div>
          )}
        </fieldset>
      </div>
    </Dialog>
  );
}

function DeleteSettingModal({
  kind,
  setting,
  onClose,
  onDone,
}: {
  kind: SettingKind;
  setting: Setting;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const write = useWrite(onDone);
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={`Delete ${setting.name}`}
      size="M"
      onClose={onClose}
      ui={`ui.billingSettings.${kind}.confirmDelete`}
      footer={
        <Footer write={write} onRefresh={() => onDone('Refreshed')}>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="danger"
            disabled={write.busy}
            onClick={() =>
              void write.run(async () => {
                await deleteSetting(kind, setting.id);
                return `${setting.name} deleted`;
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
          Deletes the {TABS[kind].word}. Issued bills keep their own copy. The server refuses while
          a billing account still names it.
        </p>
      </div>
    </Dialog>
  );
}
