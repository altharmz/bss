import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useState } from 'react';
import {
  createNumber,
  deleteNumber,
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
import { Button, Dialog, PageHeader, Panel, TextInput, useToast } from '../ui/kit';
import { Footer, useWrite } from './FinancialForms';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';

const COLUMNS: Column[] = [
  { key: 'value', label: 'Number', width: '1fr', mono: true },
  { key: 'allocation', label: 'Allocation', width: '150px' },
  { key: 'usage', label: 'Usage', width: '120px' },
  { key: 'admin', label: 'Administration', width: '160px' },
];

export function NumbersScreen() {
  useSetCrumbs([{ label: 'Numbers' }], 'numbers');
  const numbers = useResource('numbers', loadNumbers);
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<SubscriberNumber | null>(null);
  return (
    <div className="bss-screen" data-ui="ui.numbers.list">
      <div className="bss-title-row">
        <PageHeader title="Numbers" />
        <Button onClick={() => setCreating(true)}>New number</Button>
      </div>
      <TextInput
        aria-label="Search numbers"
        placeholder="Search numbers"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <Panel>
        <TableHead columns={COLUMNS} />
        <ListState
          resource={numbers}
          columns={COLUMNS}
          errorTitle="Failed to load numbers"
          empty={{ title: 'No numbers yet', text: 'Add an E.164 number to the inventory.' }}
          total={(data) => data.length}
          rows={(data) => data.filter((n) => (n.value ?? '').includes(query.trim()))}
          onResetFilters={() => setQuery('')}
        >
          {(rows) =>
            rows.map((n) => (
              <TableRow
                key={n.id}
                columns={COLUMNS}
                cells={{
                  value: <Button onClick={() => setSelected(n)}>{n.value}</Button>,
                  allocation: n.allocationStatus,
                  usage: n.usageState,
                  admin: n.administrativeState,
                }}
              />
            ))
          }
        </ListState>
      </Panel>
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
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  async function submit() {
    setBusy(true);
    try {
      await createNumber(value);
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
            disabled={busy || uncertain || !/^[1-9][0-9]{7,14}$/.test(value)}
            onClick={() => void submit()}
          >
            Create number
          </Button>
        </>
      }
    >
      <div className="bss-modal-body">
        <label className="bss-field">
          Number
          <TextInput value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <p>E.164 digits only, 8–15 digits, without a plus sign or spaces.</p>
        {problem && <div role="alert">{problem}</div>}
      </div>
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
      size="L"
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
      <div className="bss-modal-body">
        {fresh.status === 'error' && (
          <ErrorBlock title="Failed to load the number" error={fresh.error} onRetry={fresh.retry} />
        )}
        <p>
          {number.name} · {number.allocationStatus} · {number.usageState} ·{' '}
          {number.administrativeState}
        </p>
        <p>Locking prevents new assignments. Accepted assignments and current service continue.</p>
        <Button disabled={busy || uncertain} onClick={() => void toggle()}>
          {number.administrativeState === 'locked' ? 'Unlock' : 'Lock'}
        </Button>
        {problem && <div role="alert">{problem}</div>}
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
                {p.name ?? p.id} · {p.status}
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
      </div>
    </Dialog>
  );
}

/** Availability here is a hint for today's inventory. The order checks the full interval. */
export function NumberPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const numbers = useResource('number-picker', loadNumbers);
  if (numbers.status === 'error')
    return (
      <ErrorBlock title="Failed to load numbers" error={numbers.error} onRetry={numbers.retry} />
    );
  return (
    <label className="bss-field">
      Subscriber number
      <select
        aria-label="Subscriber number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={numbers.status !== 'ok'}
      >
        <option value="">Select a number</option>
        {numbers.status === 'ok' &&
          numbers.data
            .filter(
              (n) => n.administrativeState === 'unlocked' && n.allocationStatus === 'available',
            )
            .map((n) => (
              <option key={n.id} value={n.id}>
                {n.value}
              </option>
            ))}
      </select>
      <Note tone="info">
        The order checks availability for the requested dates, including future claims and
        quarantine.
      </Note>
    </label>
  );
}
