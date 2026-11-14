import { useState, type ReactNode } from 'react';
import { explain } from '../api/client';
import type { AccountProduct } from '../api/bss';
import {
  AUDIT_ACTIONS,
  AUDIT_PAGE,
  loadAudit,
  loadLock,
  loadPin,
  PIN_PATTERN,
  setLock,
  setPin,
  VERIFICATION,
  type AuditAction,
  type AuditEntry,
  type AuditOutcome,
  type LineLock,
} from '../api/security';
import { useResource, type ResourceWithRetry } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { formatInt } from '../format/money';
import {
  Button,
  Checkbox,
  Dialog,
  Field,
  KeyValue,
  Panel,
  SelectField,
  Tag,
  TextInput,
  submitDialogPrimary,
  useToast,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';

// The Security tab of the subscriber card (docs/decisions/accounts.md, issue 226): the
// customer's account PIN status with Set/Reset, the SIM swap and port-out locks of each line,
// and the read-only audit log. The server decides (protected customers, lockout, which lock
// change needs the PIN); the UI shows its refusal with the operator sentence of its code.

const LOCK_COLUMNS: Column[] = [
  { key: 'product', label: 'Line', width: 'minmax(0, 1fr)' },
  { key: 'simSwap', label: 'SIM swap', width: '110px' },
  { key: 'portOut', label: 'Port-out', width: '110px' },
  { key: 'updated', label: 'Changed', width: 'minmax(0, 1fr)' },
  { key: 'action', label: '', width: '120px', align: 'right' },
];
const AUDIT_COLUMNS: Column[] = [
  { key: 'at', label: 'When', width: '150px', mono: true },
  { key: 'operator', label: 'Who', width: '120px' },
  { key: 'action', label: 'Action', width: '160px' },
  { key: 'outcome', label: 'Outcome', width: '170px' },
  { key: 'reason', label: 'Reason', width: 'minmax(0, 1fr)' },
  { key: 'verification', label: 'Verification', width: '110px' },
  { key: 'product', label: 'Line', width: '110px' },
];
const LOCKABLE = ['created', 'pendingActive', 'active', 'suspended', 'pendingTerminate'];

/** A line's short id, as the product rows show it (PRD- and the first 8 characters). */
function LineId({ id }: { id: string }) {
  return <span className="bss-mono">PRD-{id.slice(0, 8).toUpperCase()}</span>;
}

function at(value: string, timeZone: string): string {
  return `${businessDate(value, timeZone)} ${clockTime(value, timeZone)}`;
}

/** A 4xx is a refusal (the operator may correct and retry); anything else may have been
 * saved, so the dialog stops offering the write until it is reopened. */
function useWrite() {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  async function run<T>(write: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setProblem('');
    try {
      return await write();
    } catch (error) {
      const status = (error as { status?: number }).status;
      const known = typeof status === 'number' && status >= 400 && status < 500;
      setProblem(known ? explain(error) : `${explain(error)}. Close and check before retrying.`);
      setUncertain(!known);
      return null;
    } finally {
      setBusy(false);
    }
  }
  return { busy, problem, uncertain, run };
}

function reasonError(reason: string, touched: boolean): string | undefined {
  const length = reason.trim().length;
  if (!touched) return undefined;
  if (length < 3) return 'Enter the reason (at least 3 characters)';
  if (length > 500) return 'At most 500 characters';
  return undefined;
}

/** The account PIN field the sensitive dialogs share: sent as X-Account-Pin, never stored. */
export function AccountPinField({
  value,
  onChange,
  help = "The customer's account PIN (6 to 8 digits), checked by the server for prepaid lines.",
}: {
  value: string;
  onChange: (value: string) => void;
  help?: string;
}) {
  return (
    <Field label="Account PIN" help={help}>
      <TextInput
        type="password"
        inputMode="numeric"
        aria-label="Account PIN"
        autoComplete="off"
        data-ui="ui.security.accountPin"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

// --- PIN ---

function PinDialog({
  customerId,
  reset,
  onClose,
  onDone,
}: {
  customerId: string;
  reset: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const write = useWrite();
  const [pin, setPinValue] = useState('');
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);
  const pinBad = !PIN_PATTERN.test(pin);
  const reasonBad = reasonError(reason, true) !== undefined;
  async function save() {
    setTouched(true);
    if (pinBad || reasonBad) return;
    const done = await write.run(() => setPin(customerId, pin, reason));
    if (done !== null) {
      toast(reset ? 'Account PIN reset' : 'Account PIN set');
      onDone();
    }
  }
  return (
    <Dialog
      title={reset ? 'Reset account PIN' : 'Set account PIN'}
      size="S"
      onClose={onClose}
      onSubmit={submitDialogPrimary}
      ui="ui.subscriber.security.pinDialog"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={write.busy || write.uncertain}
            onClick={() => void save()}
          >
            {reset ? 'Reset PIN' : 'Set PIN'}
          </Button>
        </>
      }
    >
      <p>
        The customer chooses the PIN and gives it to you; it is stored hashed and never shown again.
        {reset ? ' A reset also ends a lockout.' : ''} The customer is told of the change.
      </p>
      <Field
        label="New PIN"
        help="6 to 8 digits."
        error={touched && pinBad ? 'Enter 6 to 8 digits' : undefined}
      >
        <TextInput
          type="password"
          inputMode="numeric"
          aria-label="New PIN"
          autoComplete="new-password"
          value={pin}
          onChange={(e) => setPinValue(e.target.value)}
        />
      </Field>
      <Field
        label="Reason"
        help="Recorded in the audit log, e.g. identity checked at the counter."
        error={reasonError(reason, touched)}
      >
        <TextInput aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      {write.problem && <Note tone="negative">{write.problem}</Note>}
    </Dialog>
  );
}

function PinPanel({ customerId, onChanged }: { customerId: string; onChanged: () => void }) {
  const { timeZone } = useSystemContext();
  const [version, setVersion] = useState(0);
  const status = useResource(`security-pin:${customerId}:${version}`, (signal) =>
    loadPin(customerId, signal),
  );
  const [editing, setEditing] = useState(false);
  const loaded = status.status === 'ok' ? status.data : null;
  return (
    <Panel
      title="Account PIN"
      subtitle="Asked before a SIM swap, number change, Number Transfer PIN, contact change or clearing a lock on a prepaid line"
      right={
        loaded !== null ? (
          <Button small data-ui="ui.subscriber.security.setPin" onClick={() => setEditing(true)}>
            {loaded.set ? 'Reset PIN' : 'Set PIN'}
          </Button>
        ) : undefined
      }
    >
      <div data-ui="ui.subscriber.security.pin">
        {status.status === 'loading' && <p className="bss-secondary">Loading the PIN status…</p>}
        {status.status === 'error' && (
          <ErrorBlock
            title="Failed to load the PIN status"
            error={status.error}
            onRetry={status.retry}
          />
        )}
        {loaded !== null && (
          <KeyValue
            items={[
              ['Status', loaded.set ? <Tag key="s">Set</Tag> : 'Not set'],
              ...(loaded.set
                ? ([
                    [
                      'Set at',
                      loaded.setAt ? `${at(loaded.setAt, timeZone)} by ${loaded.setBy}` : '—',
                    ],
                    ['Reason', loaded.reason ?? '—'],
                    ['Wrong attempts', formatInt(loaded.failedAttempts)],
                  ] as [string, string][])
                : []),
              ...(loaded.lockedUntil
                ? ([
                    [
                      'Locked until',
                      <span key="l" className="security-negative">
                        {at(loaded.lockedUntil, timeZone)}
                      </span>,
                    ],
                  ] as [string, ReactNode][])
                : []),
            ]}
          />
        )}
        {loaded !== null && !loaded.set && (
          <Note tone="info">
            A prepaid customer with a live line needs a PIN before any of these actions.
          </Note>
        )}
      </div>
      {editing && loaded !== null && (
        <PinDialog
          customerId={customerId}
          reset={loaded.set}
          onClose={() => setEditing(false)}
          onDone={() => {
            setEditing(false);
            setVersion((v) => v + 1);
            onChanged();
          }}
        />
      )}
    </Panel>
  );
}

// --- locks ---

function LockDialog({
  product,
  current,
  prepaid,
  onClose,
  onDone,
}: {
  product: AccountProduct;
  current: LineLock;
  prepaid: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const write = useWrite();
  const [simSwap, setSimSwap] = useState(current.simSwap);
  const [portOut, setPortOut] = useState(current.portOut);
  const [reason, setReason] = useState('');
  const [accountPin, setAccountPin] = useState('');
  const [touched, setTouched] = useState(false);
  const clearing = (current.simSwap && !simSwap) || (current.portOut && !portOut);
  const unchanged = simSwap === current.simSwap && portOut === current.portOut;
  async function save() {
    setTouched(true);
    if (reasonError(reason, true) !== undefined) return;
    const done = await write.run(() =>
      setLock(product.id, { simSwap, portOut, reason }, clearing ? accountPin : ''),
    );
    if (done !== null) {
      setAccountPin('');
      toast(clearing ? 'Line lock cleared' : 'Line locks saved');
      onDone();
    }
  }
  return (
    <Dialog
      title="Line locks"
      context={product.productOffering.name}
      size="M"
      onClose={onClose}
      onSubmit={submitDialogPrimary}
      ui="ui.subscriber.security.lockDialog"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={write.busy || write.uncertain || unchanged}
            onClick={() => void save()}
          >
            Save locks
          </Button>
        </>
      }
    >
      <Checkbox
        label="SIM swap lock: refuse every SIM swap order"
        checked={simSwap}
        onChange={(e) => setSimSwap(e.target.checked)}
      />
      <Checkbox
        label="Port-out lock: issue no Number Transfer PIN, refuse the carrier's port-out"
        checked={portOut}
        onChange={(e) => setPortOut(e.target.checked)}
      />
      <p className="bss-secondary">
        A lock holds whatever PIN is given. Setting one needs no PIN; clearing one
        {prepaid ? ' needs the customer’s account PIN.' : ' is recorded like any change.'}
      </p>
      <Field
        label="Reason"
        help="Recorded with the locks and in the audit log."
        error={reasonError(reason, touched)}
      >
        <TextInput aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      </Field>
      {clearing && <AccountPinField value={accountPin} onChange={setAccountPin} />}
      {write.problem && <Note tone="negative">{write.problem}</Note>}
    </Dialog>
  );
}

function LockRow({
  product,
  prepaid,
  onChanged,
}: {
  product: AccountProduct;
  prepaid: boolean;
  onChanged: () => void;
}) {
  const { timeZone } = useSystemContext();
  const [version, setVersion] = useState(0);
  const lock = useResource(`security-lock:${product.id}:${version}`, (signal) =>
    loadLock(product.id, signal),
  );
  const [editing, setEditing] = useState(false);
  const flag = (on: boolean) => (on ? <Tag>Locked</Tag> : <span className="bss-dash">Off</span>);
  const loaded = lock.status === 'ok' ? lock.data : null;
  return (
    <>
      <TableRow
        columns={LOCK_COLUMNS}
        density="compact"
        cells={{
          product: (
            <span>
              {product.productOffering.name} <LineId id={product.id} />
            </span>
          ),
          simSwap: loaded ? flag(loaded.simSwap) : lock.status === 'error' ? '!' : '…',
          portOut: loaded ? flag(loaded.portOut) : lock.status === 'error' ? '!' : '…',
          updated:
            loaded?.updatedAt != null ? (
              <span className="bss-secondary">
                {at(loaded.updatedAt, timeZone)} by {loaded.updatedBy}
                {loaded.reason ? ` · ${loaded.reason}` : ''}
              </span>
            ) : (
              <span className="bss-dash">—</span>
            ),
          action: (
            <Button
              small
              data-ui="ui.subscriber.security.changeLocks"
              disabled={loaded === null}
              onClick={() => setEditing(true)}
            >
              Change locks
            </Button>
          ),
        }}
      />
      {lock.status === 'error' && (
        <ErrorBlock title="Failed to load the locks" error={lock.error} onRetry={lock.retry} />
      )}
      {editing && loaded !== null && (
        <LockDialog
          product={product}
          current={loaded}
          prepaid={prepaid}
          onClose={() => setEditing(false)}
          onDone={() => {
            setEditing(false);
            setVersion((v) => v + 1);
            onChanged();
          }}
        />
      )}
    </>
  );
}

function LocksPanel({
  products,
  prepaid,
  onChanged,
}: {
  products: ResourceWithRetry<AccountProduct[]>;
  prepaid: boolean;
  onChanged: () => void;
}) {
  return (
    <Panel
      title="Line locks"
      subtitle="Per line: refuse a SIM swap or a port-out, whatever the PIN"
    >
      <div data-ui="ui.subscriber.security.locks">
        <TableHead columns={LOCK_COLUMNS} density="compact" />
        <ListState
          resource={products}
          columns={LOCK_COLUMNS}
          errorTitle="Failed to load products"
          empty={{ title: 'No lines', text: 'Locks apply to the account’s live products.' }}
          count={(data) => data.filter((p) => LOCKABLE.includes(p.status)).length}
          rows={(data) => data.filter((p) => LOCKABLE.includes(p.status))}
          onResetFilters={() => {}}
        >
          {(rows: AccountProduct[]) =>
            rows.map((product) => (
              <LockRow key={product.id} product={product} prepaid={prepaid} onChanged={onChanged} />
            ))
          }
        </ListState>
      </div>
    </Panel>
  );
}

// --- audit log ---

function OutcomeCell({ entry }: { entry: AuditEntry }) {
  return entry.outcome === 'done' ? (
    <span>Done</span>
  ) : (
    <span className="security-negative">
      Refused
      {entry.reason && !entry.action.startsWith('pin_') && !entry.action.startsWith('lock_') ? (
        <span className="bss-mono"> {entry.reason}</span>
      ) : null}
    </span>
  );
}

/** A refusal's `reason` is its code (shown with the outcome); a PIN or lock change's is the
 * agent's own words. */
function reasonText(entry: AuditEntry): string | null {
  if (entry.outcome === 'refused' && !/^(pin|lock)_/.test(entry.action)) return null;
  return entry.reason;
}

/** The read-only audit log of one customer (newest first, paged), with action and outcome
 * filters. Used on the subscriber card and the Parties customer dialog. */
export function AuditLog({ customerId, version = 0 }: { customerId: string; version?: number }) {
  const { timeZone } = useSystemContext();
  const [action, setAction] = useState<AuditAction | ''>('');
  const [outcome, setOutcome] = useState<AuditOutcome | ''>('');
  const [offset, setOffset] = useState(0);
  const filter = { customerId, action, outcome };
  const audit = useResource(
    `security-audit:${customerId}:${action}:${outcome}:${offset}:${version}`,
    (signal) => loadAudit(filter, offset, signal),
  );
  const filters = [
    ...(action ? [{ label: 'Action', value: AUDIT_ACTIONS[action] }] : []),
    ...(outcome ? [{ label: 'Outcome', value: outcome === 'done' ? 'Done' : 'Refused' }] : []),
  ];
  return (
    <div data-ui="ui.security.audit">
      <div className="security-audit-filters">
        <Field label="Action">
          <SelectField
            aria-label="Action"
            data-ui="ui.security.audit.action"
            value={action}
            onChange={(e) => {
              setAction(e.target.value as AuditAction | '');
              setOffset(0);
            }}
          >
            <option value="">All actions</option>
            {Object.entries(AUDIT_ACTIONS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </SelectField>
        </Field>
        <Field label="Outcome">
          <SelectField
            aria-label="Outcome"
            data-ui="ui.security.audit.outcome"
            value={outcome}
            onChange={(e) => {
              setOutcome(e.target.value as AuditOutcome | '');
              setOffset(0);
            }}
          >
            <option value="">All outcomes</option>
            <option value="done">Done</option>
            <option value="refused">Refused</option>
          </SelectField>
        </Field>
      </div>
      <TableHead columns={AUDIT_COLUMNS} density="compact" />
      <ListState
        resource={audit}
        columns={AUDIT_COLUMNS}
        errorTitle="Failed to load the audit log"
        empty={{ title: 'No entries', text: 'No agent action on this customer is recorded yet.' }}
        filters={filters}
        // an empty filtered page is "no matches" (with Reset), not "nothing recorded"
        count={(data) => data.items.length || filters.length}
        rows={(data) => data.items}
        onResetFilters={() => {
          setAction('');
          setOutcome('');
          setOffset(0);
        }}
      >
        {(rows: AuditEntry[]) => (
          <>
            {rows.map((entry) => (
              <TableRow
                key={entry.id}
                columns={AUDIT_COLUMNS}
                density="compact"
                cells={{
                  at: at(entry.at, timeZone),
                  operator: entry.operator,
                  action: AUDIT_ACTIONS[entry.action] ?? entry.action,
                  outcome: <OutcomeCell entry={entry} />,
                  reason: reasonText(entry) ?? <span className="bss-dash">—</span>,
                  verification: VERIFICATION[entry.verification] ?? entry.verification,
                  product: entry.product ? (
                    <LineId id={entry.product.id} />
                  ) : (
                    <span className="bss-dash">—</span>
                  ),
                }}
              />
            ))}
            {audit.status === 'ok' && (
              <TableFooter
                left={`Showing ${formatInt(offset + 1)}–${formatInt(offset + rows.length)}`}
                right={
                  <span className="bss-table-pager">
                    <Button
                      small
                      data-ui="ui.security.audit.previous"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE))}
                    >
                      Previous
                    </Button>
                    <Button
                      small
                      data-ui="ui.security.audit.next"
                      disabled={!audit.data.more}
                      onClick={() => setOffset(offset + AUDIT_PAGE)}
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
    </div>
  );
}

/** The tab body. Mounted only while shown, so the card makes no security request for a
 * subscriber whose Security tab is never opened. */
export function SecurityTab({
  customerId,
  prepaid,
  products,
}: {
  customerId: string;
  prepaid: boolean;
  products: ResourceWithRetry<AccountProduct[]>;
}) {
  // a PIN or lock change writes an audit entry: show it
  const [version, setVersion] = useState(0);
  const changed = () => setVersion((v) => v + 1);
  return (
    <section data-ui="ui.subscriber.security">
      <PinPanel customerId={customerId} onChanged={changed} />
      <LocksPanel products={products} prepaid={prepaid} onChanged={changed} />
      <Panel title="Audit log" subtitle="Agent actions on this customer, newest first; read only">
        <AuditLog customerId={customerId} version={version} />
      </Panel>
    </section>
  );
}
