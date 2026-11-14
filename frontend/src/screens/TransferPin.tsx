import { useState } from 'react';
import { createPin, loadPins, type TransferPin } from '../api/ports';
import { explain } from '../api/client';
import { businessDate } from '../format/dates';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { Button, Dialog, submitDialogPrimary, useToast } from '../ui/kit';
import { AccountPinField } from './SecurityTab';
import { Note } from '../ui/Note';
import { ErrorBlock } from '../ui/states';

/** Number Transfer PIN of a line: generated on request, shown once with its expiry. */
export function TransferPinDialog({
  productId,
  onClose,
}: {
  productId: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const { timeZone } = useSystemContext();
  const pins = useResource(`pins:${productId}`, (signal) => loadPins(productId, signal));
  const [shown, setShown] = useState<TransferPin | null>(null);
  const [accountPin, setAccountPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  async function generate() {
    setBusy(true);
    setProblem('');
    try {
      setShown(await createPin(productId, accountPin.trim()));
      setAccountPin('');
      pins.retry();
    } catch (error) {
      const status = (error as { status?: number }).status;
      const known = typeof status === 'number' && status >= 400 && status < 500;
      setProblem(
        known ? explain(error) : `${explain(error)}. Check the PIN list before generating again.`,
      );
      setUncertain(!known);
    } finally {
      setBusy(false);
    }
  }
  async function copy(pin: string) {
    try {
      await navigator.clipboard.writeText(pin);
      toast('PIN copied');
    } catch {
      toast('Copy failed: select the PIN and copy it by hand');
    }
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Number Transfer PIN"
      size="M"
      onClose={onClose}
      ui="ui.subscriber.transferPin"
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button data-dialog-primary disabled={busy || uncertain} onClick={() => void generate()}>
            {shown ? 'Generate a new PIN' : 'Generate PIN'}
          </Button>
        </>
      }
    >
      <>
        {shown?.pin && (
          <Note tone="warning" lead="Shown once.">
            <p>
              PIN for {shown.number}:{' '}
              <strong className="bss-mono" data-testid="transfer-pin">
                {shown.pin}
              </strong>{' '}
              <Button small onClick={() => void copy(shown.pin ?? '')}>
                Copy
              </Button>
            </p>
            <p>
              Valid until {businessDate(shown.expiresAt, timeZone)}, single use. It cannot be shown
              again; a new PIN replaces this one.
            </p>
          </Note>
        )}
        {!shown && (
          <p>
            Give the subscriber a PIN to take their number to another carrier. A new PIN revokes the
            previous one.
          </p>
        )}
        <AccountPinField value={accountPin} onChange={setAccountPin} />
        {pins.status === 'error' && (
          <ErrorBlock title="Failed to load PINs" error={pins.error} onRetry={pins.retry} />
        )}
        {pins.status === 'ok' &&
          pins.data.map((p) => (
            <p key={p.id}>
              {p.number} · {p.state} · issued {businessDate(p.createdAt, timeZone)} · expires{' '}
              {businessDate(p.expiresAt, timeZone)}
            </p>
          ))}
        {problem && <Note tone="negative">{problem}</Note>}
      </>
    </Dialog>
  );
}
