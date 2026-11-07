import { useState } from 'react';
import { requestPool } from '../api/ports';
import { explain } from '../api/client';
import { plural } from '../format/labels';
import { Button, Dialog, Field, TextInput, submitDialogPrimary, useToast } from '../ui/kit';
import { Note } from '../ui/Note';

const AREA_CODE = /^[2-9][0-9]{2}$/;

/** Asks the carrier for numbers of an area code; they join the inventory unlocked. */
export function PoolDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [areaCode, setAreaCode] = useState('');
  const [count, setCount] = useState('10');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const amount = Number(count);
  const valid = AREA_CODE.test(areaCode) && /^[0-9]+$/.test(count) && amount >= 1 && amount <= 100;
  async function submit() {
    setBusy(true);
    setProblem('');
    try {
      const result = await requestPool(areaCode, amount);
      toast(`${plural(result.numbers.length, 'number')} added for area code ${result.areaCode}`);
      onDone();
    } catch (error) {
      const status = (error as { status?: number }).status;
      const known = typeof status === 'number' && status >= 400 && status < 500;
      setProblem(
        known ? explain(error) : `${explain(error)}. Check the numbers before requesting again.`,
      );
      setUncertain(!known);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Request numbers"
      size="M"
      onClose={onClose}
      ui="ui.numbers.poolDialog"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={busy || uncertain || !valid}
            onClick={() => void submit()}
          >
            Request
          </Button>
        </>
      }
    >
      <Field label="Area code">
        <TextInput mono value={areaCode} onChange={(e) => setAreaCode(e.target.value)} />
      </Field>
      <Field label="How many">
        <TextInput mono value={count} onChange={(e) => setCount(e.target.value)} />
      </Field>
      <p className="bss-caption">
        Three digits (first 2 to 9), 1 to 100 numbers, asked from the carrier.
      </p>
      {problem && <Note tone="negative">{problem}</Note>}
    </Dialog>
  );
}
