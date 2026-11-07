import { useState } from 'react';
import {
  cancelPort,
  loadPort,
  loadPorts,
  PORT_STATES,
  resubmitPort,
  type PortCorrection,
  type PortDirection,
  type PortRequest,
  type PortState,
} from '../api/ports';
import { explain } from '../api/client';
import { businessDate } from '../format/dates';
import { useResource } from '../api/useResource';
import { useSetCrumbs, useSystemContext } from '../app/context';
import { Link } from '../app/router';
import {
  Button,
  Dialog,
  Field,
  KeyValue,
  PageHeader,
  SelectField,
  Tag,
  TextInput,
  submitDialogPrimary,
  useToast,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';

const COLUMNS: Column[] = [
  { key: 'number', label: 'Number', width: '1fr', mono: true },
  { key: 'direction', label: 'Direction', width: '100px' },
  { key: 'state', label: 'State', width: '120px' },
  { key: 'reason', label: 'Reason', width: '1.6fr' },
  { key: 'created', label: 'Created', width: '120px' },
];

export const PORT_STATE_LABEL: Record<PortState, string> = {
  submitted: 'Submitted',
  pending: 'Pending',
  rejected: 'Rejected',
  completed: 'Completed',
  cancelled: 'Cancelled',
};
const DIRECTION_LABEL: Record<PortDirection, string> = { in: 'Port-in', out: 'Port-out' };

/** What the carrier or the BSS said about a request that did not go through. */
export function portReason(port: PortRequest): string {
  if (port.rejectionReason) {
    return port.rejectionCode
      ? `${port.rejectionReason} (${port.rejectionCode})`
      : port.rejectionReason;
  }
  return port.rejectionCode ?? port.lastError ?? '—';
}

export function PortRequestsScreen() {
  useSetCrumbs([{ label: 'Port requests' }], 'ports');
  const { timeZone } = useSystemContext();
  const [direction, setDirection] = useState<'' | PortDirection>('');
  const [state, setState] = useState<'' | PortState>('');
  const [number, setNumber] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const ports = useResource(`ports:${direction}:${state}:${number.trim()}`, (signal) =>
    loadPorts({ direction, state, number: number.trim() }, signal),
  );
  return (
    <div className="bss-screen" data-ui="ui.ports.list">
      <PageHeader
        title="Port requests"
        count={ports.status === 'ok' ? ports.data.length : undefined}
        filters={
          <>
            <TextInput
              aria-label="Number"
              placeholder="Number"
              value={number}
              onChange={(e) => setNumber(e.target.value)}
            />
            <SelectField
              aria-label="Direction"
              value={direction}
              onChange={(e) => setDirection(e.target.value as '' | PortDirection)}
            >
              <option value="">Both directions</option>
              <option value="in">Port-in</option>
              <option value="out">Port-out</option>
            </SelectField>
            <SelectField
              aria-label="State"
              value={state}
              onChange={(e) => setState(e.target.value as '' | PortState)}
            >
              <option value="">All states</option>
              {PORT_STATES.map((s) => (
                <option key={s} value={s}>
                  {PORT_STATE_LABEL[s]}
                </option>
              ))}
            </SelectField>
          </>
        }
      />
      <div role="table" aria-label="Port requests" className="bss-page-table">
        <TableHead columns={COLUMNS} />
        <ListState
          resource={ports}
          columns={COLUMNS}
          errorTitle="Failed to load port requests"
          empty={{
            title: 'No port requests yet',
            text: 'A port-in is created from Add product; a port-out arrives from the carrier.',
          }}
          total={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => {
            setNumber('');
            setDirection('');
            setState('');
          }}
        >
          {(rows) =>
            rows.map((p) => (
              <TableRow
                key={p.id}
                columns={COLUMNS}
                cells={{
                  number: <Button onClick={() => setSelected(p.id)}>{p.number}</Button>,
                  direction: DIRECTION_LABEL[p.direction],
                  state: <Tag>{PORT_STATE_LABEL[p.state]}</Tag>,
                  reason: portReason(p),
                  created: businessDate(p.createdAt, timeZone),
                }}
              />
            ))
          }
        </ListState>
      </div>
      {selected && (
        <PortDetail
          id={selected}
          onClose={() => setSelected(null)}
          onChanged={() => ports.retry()}
        />
      )}
    </div>
  );
}

function PortDetail({
  id,
  onClose,
  onChanged,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const fresh = useResource(`port:${id}`, (signal) => loadPort(id, signal));
  const port = fresh.status === 'ok' ? fresh.data : null;
  const [correcting, setCorrecting] = useState(false);
  const [pin, setPin] = useState('');
  const [account, setAccount] = useState<string | null>(null);
  const [zip, setZip] = useState<string | null>(null);
  const [first, setFirst] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const open =
    port?.direction === 'in' && ['submitted', 'pending', 'rejected'].includes(port.state);

  async function run(action: () => Promise<PortRequest>, done: string) {
    setBusy(true);
    setProblem('');
    try {
      await action();
      toast(done);
      onChanged();
      onClose();
    } catch (error) {
      // a 4xx changed nothing; anything else may have
      const status = (error as { status?: number }).status;
      const known = typeof status === 'number' && status >= 400 && status < 500;
      setProblem(
        known ? explain(error) : `${explain(error)}. Reload the request before trying again.`,
      );
      setUncertain(!known);
    } finally {
      setBusy(false);
    }
  }
  function resubmit() {
    if (!port) return;
    // only what the operator changed is sent: the server keeps the rest
    const body: PortCorrection = { transferPin: pin.trim() };
    if (account !== null && account.trim() !== (port.carrierAccountNumber ?? '')) {
      body.carrierAccountNumber = account.trim();
    }
    if (zip !== null && zip.trim() !== (port.billingZip ?? '')) body.billingZip = zip.trim();
    if (first !== null && first.trim() !== (port.firstName ?? '')) body.firstName = first.trim();
    if (last !== null && last.trim() !== (port.lastName ?? '')) body.lastName = last.trim();
    void run(() => resubmitPort(port.id, body), 'Port-in resubmitted');
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={port ? `${DIRECTION_LABEL[port.direction]} ${port.number}` : 'Port request'}
      size="M"
      onClose={onClose}
      ui="ui.ports.detail"
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          {port?.state === 'rejected' && port.direction === 'in' && !correcting && (
            <Button data-ui="ui.ports.resubmit" onClick={() => setCorrecting(true)}>
              Correct and resubmit
            </Button>
          )}
          {correcting && (
            <Button
              data-dialog-primary
              disabled={busy || uncertain || pin.trim() === ''}
              onClick={resubmit}
            >
              Resubmit
            </Button>
          )}
          {open && !correcting && (
            <Button
              tone="negative"
              data-ui="ui.ports.cancel"
              disabled={busy || uncertain}
              onClick={() => void run(() => cancelPort(id), 'Port-in cancelled')}
            >
              Cancel port-in
            </Button>
          )}
        </>
      }
    >
      <>
        {fresh.status === 'error' && (
          <ErrorBlock
            title="Failed to load the request"
            error={fresh.error}
            onRetry={fresh.retry}
          />
        )}
        {port && (
          <KeyValue
            items={[
              ['Number', port.number],
              ['Direction', DIRECTION_LABEL[port.direction]],
              ['State', PORT_STATE_LABEL[port.state]],
              ['Reason', portReason(port)],
              ['Submissions', String(port.submissions)],
              ['Carrier reference', port.carrierReference ?? '—'],
              ...(port.direction === 'in'
                ? ([
                    ['Carrier account', port.carrierAccountNumber ?? '—'],
                    ['Billing ZIP', port.billingZip ?? '—'],
                    ['Name', [port.firstName, port.lastName].filter(Boolean).join(' ') || '—'],
                  ] as [string, string][])
                : []),
            ]}
          />
        )}
        {port?.billingAccount && (
          <p>
            <Link href={`/subscribers/${port.billingAccount.id}`}>Open subscriber</Link>
          </p>
        )}
        {port?.state === 'rejected' && !correcting && port.direction === 'in' && (
          <Note tone="info">
            Correct the data the carrier refused and enter the losing carrier&apos;s Number Transfer
            PIN again: the BSS does not keep it.
          </Note>
        )}
        {correcting && port && (
          <>
            <Field label="Number Transfer PIN">
              <TextInput
                mono
                autoComplete="off"
                value={pin}
                onChange={(e) => setPin(e.target.value)}
              />
            </Field>
            <Field label="Carrier account number">
              <TextInput
                value={account ?? port.carrierAccountNumber ?? ''}
                onChange={(e) => setAccount(e.target.value)}
              />
            </Field>
            <Field label="Billing ZIP">
              <TextInput
                mono
                value={zip ?? port.billingZip ?? ''}
                onChange={(e) => setZip(e.target.value)}
              />
            </Field>
            <Field label="First name">
              <TextInput
                value={first ?? port.firstName ?? ''}
                onChange={(e) => setFirst(e.target.value)}
              />
            </Field>
            <Field label="Last name">
              <TextInput
                value={last ?? port.lastName ?? ''}
                onChange={(e) => setLast(e.target.value)}
              />
            </Field>
          </>
        )}
        {problem && <Note tone="negative">{problem}</Note>}
      </>
    </Dialog>
  );
}
