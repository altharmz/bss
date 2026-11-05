import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { Combobox } from '../ui/combobox';
import { useState } from 'react';
import {
  createNumber,
  deleteNumber,
  iccidOf,
  kindOf,
  type ResourceKind,
  loadNumber,
  loadNumbers,
  lockNumber,
  loadNumberProducts,
  type SubscriberNumber,
} from '../api/numbers';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { Link } from '../app/router';
import {
  Button,
  Dialog,
  Field,
  KeyValue,
  PageHeader,
  SelectField,
  StatusPill,
  Tag,
  TextInput,
  useToast,
} from '../ui/kit';
import { Footer, useWrite } from './FinancialForms';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';

const COLUMNS: Column[] = [
  { key: 'value', label: 'Number', width: '1fr', mono: true },
  { key: 'kind', label: 'Kind', width: '90px' },
  { key: 'allocation', label: 'Allocation', width: '150px' },
  { key: 'usage', label: 'Usage', width: '120px' },
  { key: 'admin', label: 'Administration', width: '160px' },
];

export function NumbersScreen() {
  useSetCrumbs([{ label: 'Numbers' }], 'numbers');
  const numbers = useResource('numbers', loadNumbers);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<'' | ResourceKind>('');
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<SubscriberNumber | null>(null);
  return (
    <div className="bss-screen" data-ui="ui.numbers.list">
      <PageHeader
        title="Numbers"
        count={numbers.status === 'ok' ? numbers.data.length : undefined}
        actions={<Button onClick={() => setCreating(true)}>New number</Button>}
        filters={
          <>
            <TextInput
              aria-label="Search numbers"
              placeholder="Search numbers"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <SelectField
              aria-label="Kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as '' | ResourceKind)}
            >
              <option value="">All kinds</option>
              <option value="msisdn">MSISDN</option>
              <option value="imsi">IMSI</option>
            </SelectField>
          </>
        }
      />
      <div role="table" aria-label="Numbers" className="bss-page-table">
        <TableHead columns={COLUMNS} />
        <ListState
          resource={numbers}
          columns={COLUMNS}
          errorTitle="Failed to load numbers"
          empty={{ title: 'No numbers yet', text: 'Add an E.164 number to the inventory.' }}
          total={(data) => data.length}
          rows={(data) =>
            data.filter(
              (n) => (n.value ?? '').includes(query.trim()) && (!kind || kindOf(n) === kind),
            )
          }
          onResetFilters={() => {
            setQuery('');
            setKind('');
          }}
        >
          {(rows) =>
            rows.map((n) => (
              <TableRow
                key={n.id}
                columns={COLUMNS}
                cells={{
                  value: <Button onClick={() => setSelected(n)}>{n.value}</Button>,
                  kind: <Tag>{kindOf(n) === 'imsi' ? 'IMSI' : 'MSISDN'}</Tag>,
                  allocation: n.allocationStatus && <Tag>{n.allocationStatus}</Tag>,
                  usage: n.usageState && <Tag>{n.usageState}</Tag>,
                  admin: n.administrativeState && <Tag>{n.administrativeState}</Tag>,
                }}
              />
            ))
          }
        </ListState>
      </div>
      {creating && (
        <NewNumber
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            numbers.retry();
          }}
        />
      )}
      {selected && (
        <NumberDetail
          number={selected}
          onClose={() => {
            setSelected(null);
            numbers.retry();
          }}
          onChanged={(n) => {
            setSelected(n);
            numbers.retry();
          }}
        />
      )}
    </div>
  );
}

function NewNumber({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [value, setValue] = useState('');
  const [kind, setKind] = useState<ResourceKind>('msisdn');
  const [iccid, setIccid] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const valid =
    kind === 'imsi'
      ? /^[0-9]{14,15}$/.test(value) && (iccid === '' || /^89[0-9]{16,18}$/.test(iccid))
      : /^[1-9][0-9]{7,14}$/.test(value);
  async function submit() {
    setBusy(true);
    try {
      await createNumber(value, kind, iccid);
      onCreated();
    } catch (error) {
      setProblem(`${explain(error)}. Close and check inventory before submitting again.`);
      setUncertain(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="New number"
      size="M"
      onClose={onClose}
      ui="ui.numbers.create"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={busy || uncertain || !valid}
            onClick={() => void submit()}
          >
            Create number
          </Button>
        </>
      }
    >
      <Field label="Kind">
        <SelectField
          aria-label="Kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as ResourceKind)}
        >
          <option value="msisdn">MSISDN</option>
          <option value="imsi">IMSI</option>
        </SelectField>
      </Field>
      <Field label={kind === 'imsi' ? 'IMSI' : 'Number'}>
        <TextInput mono value={value} onChange={(e) => setValue(e.target.value)} />
      </Field>
      {kind === 'imsi' ? (
        <>
          <Field label="ICCID (optional)">
            <TextInput mono value={iccid} onChange={(e) => setIccid(e.target.value)} />
          </Field>
          <p className="bss-caption">
            IMSI: 14–15 digits. ICCID: 18–20 digits starting with 89; it cannot change once the IMSI
            is assigned.
          </p>
        </>
      ) : (
        <p className="bss-caption">
          E.164 digits only, 8–15 digits, without a plus sign or spaces.
        </p>
      )}
      {problem && <Note tone="negative">{problem}</Note>}
    </Dialog>
  );
}

function NumberDetail({
  number: listed,
  onClose,
  onChanged,
}: {
  number: SubscriberNumber;
  onClose: () => void;
  onChanged: (n: SubscriberNumber) => void;
}) {
  // the detail is GET resource/{id}; the list row shows until it arrives
  const fresh = useResource(`number:${listed.id}:${listed.administrativeState}`, (signal) =>
    loadNumber(listed.id, signal),
  );
  const number = fresh.status === 'ok' ? fresh.data : listed;
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const remove = useWrite((message) => {
    toast(message);
    onClose();
  });
  const products = useResource(`number-products:${number.id}`, (signal) =>
    loadNumberProducts(number.id, signal),
  );
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  // hidden only when the loaded data shows an assignment; the server decides the rest
  const neverAssigned =
    products.status === 'ok' &&
    products.data.length === 0 &&
    number.allocationStatus === 'available' &&
    number.usageState === 'idle';
  async function toggle() {
    setBusy(true);
    try {
      onChanged(await lockNumber(number.id, number.administrativeState !== 'locked'));
    } catch (error) {
      setProblem(`${explain(error)}. Reopen this number to check its state.`);
      setUncertain(true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={number.value ?? 'Number'}
      size="M"
      onClose={onClose}
      ui="ui.numbers.detail"
      footer={
        <Footer write={remove} onRefresh={onClose}>
          {confirming && (
            <Button
              data-ui="ui.numbers.deleteConfirm"
              disabled={remove.busy || remove.unsure}
              onClick={() =>
                void remove.run(async () => {
                  await deleteNumber(number.id);
                  return `Number ${number.value} deleted`;
                })
              }
            >
              Confirm delete
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </Footer>
      }
    >
      <>
        {fresh.status === 'error' && (
          <ErrorBlock title="Failed to load the number" error={fresh.error} onRetry={fresh.retry} />
        )}
        <KeyValue
          items={[
            ['Name', number.name ?? '—'],
            ['Kind', kindOf(number) === 'imsi' ? 'IMSI' : 'MSISDN'],
            ...(iccidOf(number) ? [['ICCID', iccidOf(number) as string] as [string, string]] : []),
            ['Allocation', number.allocationStatus ?? '—'],
            ['Usage', number.usageState ?? '—'],
            ['Administration', number.administrativeState ?? '—'],
          ]}
        />
        <p>Locking prevents new assignments. Accepted assignments and current service continue.</p>
        <Button disabled={busy || uncertain} onClick={() => void toggle()}>
          {number.administrativeState === 'locked' ? 'Unlock' : 'Lock'}
        </Button>
        {problem && <Note tone="negative">{problem}</Note>}
        {neverAssigned && (
          <>
            <Button
              data-ui="ui.numbers.delete"
              disabled={remove.busy}
              onClick={() => setConfirming(true)}
            >
              Delete number
            </Button>
            {confirming && (
              <p>
                Delete {number.value} from the inventory? This cannot be undone; the server refuses
                a number that has ever been assigned.
              </p>
            )}
          </>
        )}
        {products.status === 'error' && (
          <ErrorBlock
            title="Failed to load assigned products"
            error={products.error}
            onRetry={products.retry}
          />
        )}
        {products.status === 'loading' && <p>Loading assigned products…</p>}
        {products.status === 'ok' &&
          (products.data.length ? (
            products.data.map((p) => (
              <p key={p.id}>
                {p.name ?? p.id} <StatusPill kind="product" status={p.status ?? ''} />
                {p.billingAccount?.id && (
                  <>
                    {' '}
                    · <Link href={`/subscribers/${p.billingAccount.id}`}>Open subscriber</Link>
                  </>
                )}
              </p>
            ))
          ) : (
            <p>No current or future product assignment.</p>
          ))}
      </>
    </Dialog>
  );
}

/** The number as the inventory shows it, plus its name when the name adds something: a
 * generated "MSISDN <number>" name repeats the number and is left out. */
function numberLabel(n: SubscriberNumber): string {
  const value = n.value ?? n.id;
  return n.name && n.name !== value && !n.name.includes(value) ? `${value} · ${n.name}` : value;
}

/** Availability here is a hint for today's inventory. The order checks the full interval. */
export function NumberPicker({
  value,
  onChange,
  kind = 'msisdn',
}: {
  value: string;
  onChange: (id: string) => void;
  kind?: ResourceKind;
}) {
  const numbers = useResource('number-picker', loadNumbers);
  const label = kind === 'imsi' ? 'IMSI' : 'Subscriber number';
  if (numbers.status === 'error')
    return (
      <ErrorBlock title="Failed to load numbers" error={numbers.error} onRetry={numbers.retry} />
    );
  const options =
    numbers.status === 'ok'
      ? numbers.data
          .filter(
            (n) =>
              kindOf(n) === kind &&
              n.administrativeState === 'unlocked' &&
              n.allocationStatus === 'available',
          )
          .map((n) => ({
            value: n.id,
            label: numberLabel(n),
          }))
      : [];
  return (
    <Field label={label}>
      <Combobox
        ariaLabel={label}
        placeholder={kind === 'imsi' ? 'Choose an IMSI' : 'Choose a number'}
        value={value}
        options={options}
        onChange={onChange}
        disabled={numbers.status !== 'ok'}
      />
      <Note tone="info">
        The order checks availability for the requested dates, including future claims and
        quarantine.
      </Note>
    </Field>
  );
}
