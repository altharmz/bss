import { useState } from 'react';
import {
  createNumber,
  loadNumbers,
  lockNumber,
  loadNumberProducts,
  type SubscriberNumber,
} from '../api/numbers';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { Link } from '../app/router';
import { Button, Modal, PageTitle, Panel, TextInput } from '../ui/kit';
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
        <PageTitle title="Numbers" />
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
    <Modal
      title="New number"
      width={560}
      onClose={onClose}
      ui="ui.numbers.create"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
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
    </Modal>
  );
}

function NumberDetail({
  number,
  onClose,
  onChanged,
}: {
  number: SubscriberNumber;
  onClose: () => void;
  onChanged: (n: SubscriberNumber) => void;
}) {
  const products = useResource(`number-products:${number.id}`, (signal) =>
    loadNumberProducts(number.id, signal),
  );
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
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
    <Modal
      title={number.value ?? 'Number'}
      width={640}
      onClose={onClose}
      ui="ui.numbers.detail"
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <div className="bss-modal-body">
        <p>
          {number.name} · {number.allocationStatus} · {number.usageState} ·{' '}
          {number.administrativeState}
        </p>
        <p>Locking prevents new assignments. Accepted assignments and current service continue.</p>
        <Button disabled={busy || uncertain} onClick={() => void toggle()}>
          {number.administrativeState === 'locked' ? 'Unlock' : 'Lock'}
        </Button>
        {problem && <div role="alert">{problem}</div>}
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
    </Modal>
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
      <span className="bss-hint">
        The order checks availability for the requested dates, including future claims and
        quarantine.
      </span>
    </label>
  );
}
