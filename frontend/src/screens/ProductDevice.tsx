import { useState } from 'react';
import { explain } from '../api/client';
import type { PendingOrder } from '../api/commands';
import {
  changeDevice,
  checkDevice,
  deviceWarning,
  imeiProblem,
  loadDevices,
  type DeviceCheck,
  type ProductDevice,
} from '../api/devices';
import { orderRefusal } from '../api/orderErrors';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate } from '../format/dates';
import { Button, Dialog, Field, TextInput, submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock } from '../ui/states';
import { Footer } from './FinancialForms';

const COMPATIBILITY: Record<DeviceCheck['result'], string> = {
  compatible: 'Works on the network',
  unknown: 'Not confirmed by the network',
  incompatible: 'Does not work on the network',
};

/** The device (IMEI) of a prepaid line: the current one and its history, Check device (asks
 * the network, stores nothing) and Change device (a modify order with only the IMEI). */
export function DeviceDialog({
  accountId,
  productId,
  canChange,
  onClose,
  onDone,
}: {
  accountId: string;
  productId: string;
  /** Change device is offered (a started prepaid line); the server decides the rest. */
  canChange: boolean;
  onClose: () => void;
  onDone: (message: string, accepted?: PendingOrder) => void;
}) {
  const { timeZone } = useSystemContext();
  const devices = useResource(`devices:${productId}`, (signal) => loadDevices(productId, signal));
  const [imei, setImei] = useState('');
  const [checked, setChecked] = useState<DeviceCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [code, setCode] = useState<string | undefined>();
  const [uncertain, setUncertain] = useState(false);
  const typed = imei.trim();
  const typedProblem = typed === '' ? null : imeiProblem(typed);
  const valid = typed !== '' && typedProblem === null;
  const history = devices.status === 'ok' ? devices.data : [];
  const current = history.find((d) => d.endedAt === null);

  async function check() {
    setBusy(true);
    setProblem('');
    setCode(undefined);
    setChecked(null);
    try {
      setChecked(await checkDevice(typed));
    } catch (error) {
      setProblem(explain(error));
    } finally {
      setBusy(false);
    }
  }

  async function change() {
    setBusy(true);
    setProblem('');
    setCode(undefined);
    try {
      const result = await changeDevice(accountId, productId, typed);
      const warning = deviceWarning(result.productOrderItem);
      const suffix = warning === null ? '' : ` · ${warning}`;
      if (result.state === 'completed') {
        onClose();
        onDone(`Device changed through an order${suffix}`);
      } else if (result.state === 'inProgress') {
        onClose();
        onDone(`Device change accepted, the billing worker completes it shortly${suffix}`, {
          id: result.id,
          label: 'Device change',
        });
      } else {
        const refusal = orderRefusal(result.productOrderErrorMessage ?? [], result.state);
        setProblem(refusal.problem);
        setCode(refusal.code);
      }
    } catch (error) {
      setProblem(`${explain(error)}. Check the device history before submitting again.`);
      setUncertain(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Device"
      size="M"
      onClose={onClose}
      ui="ui.subscriber.device"
      footer={
        <Footer
          write={{ busy, problem: problem || null, unsure: uncertain, code }}
          onRefresh={onClose}
        >
          <Button onClick={onClose}>Close</Button>
          <Button
            data-ui="ui.subscriber.checkDevice"
            disabled={busy || !valid}
            onClick={() => void check()}
          >
            Check device
          </Button>
          {canChange && (
            <Button
              data-dialog-primary
              data-ui="ui.subscriber.changeDevice"
              disabled={busy || uncertain || !valid || typed === current?.imei}
              onClick={() => void change()}
            >
              Change device
            </Button>
          )}
        </Footer>
      }
    >
      <fieldset disabled={busy} className="bss-write-fields bss-modal-body">
        {devices.status === 'loading' && <p>Loading device…</p>}
        {devices.status === 'error' && (
          <ErrorBlock
            title="Failed to load devices"
            error={devices.error}
            onRetry={devices.retry}
          />
        )}
        {devices.status === 'ok' &&
          (current ? (
            <CurrentDevice device={current} timeZone={timeZone} />
          ) : (
            <p>No device is recorded for this line.</p>
          ))}
        <Field
          label="IMEI"
          help={
            typedProblem === null
              ? canChange
                ? 'Check asks the network and stores nothing. Change records the device through an order; an incompatible device is refused.'
                : 'Check asks the network and stores nothing.'
              : undefined
          }
          error={typedProblem ?? undefined}
        >
          <TextInput
            aria-label="IMEI"
            inputMode="numeric"
            autoComplete="off"
            maxLength={15}
            value={imei}
            onChange={(e) => {
              setImei(e.target.value);
              setChecked(null);
            }}
          />
        </Field>
        {checked !== null && (
          <Note
            tone={
              checked.result === 'compatible'
                ? 'info'
                : checked.result === 'unknown'
                  ? 'warning'
                  : 'negative'
            }
            lead={`${checked.imei}: ${COMPATIBILITY[checked.result]}.`}
          >
            {checked.reasons.length > 0 && <p>{checked.reasons.join('; ')}</p>}
          </Note>
        )}
        {history.length > 0 && (
          <section className="bss-dialog-section" data-ui="ui.subscriber.deviceHistory">
            <h3>History</h3>
            {history.map((d) => (
              <p key={d.id}>
                <span className="bss-mono">{d.imei}</span> · {COMPATIBILITY[d.compatibility]} ·{' '}
                {businessDate(d.startedAt, timeZone)} –{' '}
                {d.endedAt === null ? 'now' : businessDate(d.endedAt, timeZone)}
              </p>
            ))}
          </section>
        )}
      </fieldset>
    </Dialog>
  );
}

function CurrentDevice({ device, timeZone }: { device: ProductDevice; timeZone: string }) {
  return (
    <>
      <p>
        IMEI <span className="bss-mono">{device.imei}</span> · {COMPATIBILITY[device.compatibility]}{' '}
        · since {businessDate(device.startedAt, timeZone)}
      </p>
      {device.compatibility === 'unknown' && device.reasons.length > 0 && (
        <Note tone="warning">{device.reasons.join('; ')}</Note>
      )}
    </>
  );
}
