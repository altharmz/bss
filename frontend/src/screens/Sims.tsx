import { useState } from 'react';
import {
  generateEsims,
  importCsv,
  loadBatches,
  loadSim,
  loadSims,
  simIccid,
  simState,
  simType,
  SIM_STATES,
  type Sim,
  type SimState,
  type SimType,
} from '../api/sims';
import { loadNumberProducts } from '../api/numbers';
import { explain } from '../api/client';
import { plural } from '../format/labels';
import { useResource } from '../api/useResource';
import { useSetCrumbs } from '../app/context';
import { Link } from '../app/router';
import {
  Button,
  Dialog,
  Field,
  FileField,
  KeyValue,
  PageHeader,
  SelectField,
  StatusPill,
  Tag,
  TextInput,
  submitDialogPrimary,
  useToast,
} from '../ui/kit';
import { Note } from '../ui/Note';
import { ErrorBlock, ListState } from '../ui/states';
import { TableHead, TableRow, type Column } from '../ui/Table';
import { SimActivationCode } from './SimActivationCode';

const COLUMNS: Column[] = [
  { key: 'imsi', label: 'IMSI', width: '1fr', mono: true },
  { key: 'iccid', label: 'ICCID', width: '1.2fr', mono: true },
  { key: 'type', label: 'Type', width: '110px' },
  { key: 'state', label: 'State', width: '120px' },
  { key: 'batch', label: 'Batch', width: '1fr' },
];

export const STATE_LABEL: Record<SimState, string> = {
  in_stock: 'In stock',
  reserved: 'Reserved',
  active: 'Active',
  retired: 'Retired',
};
const TYPE_LABEL: Record<SimType, string> = { physical: 'Physical', esim: 'eSIM' };

const BATCH_NAME = /^[A-Za-z0-9._-]{1,60}$/;

export function SimsScreen() {
  useSetCrumbs([{ label: 'SIMs' }], 'sims');
  const [state, setState] = useState<'' | SimState>('');
  const [type, setType] = useState<'' | SimType>('');
  const [batch, setBatch] = useState('');
  const [query, setQuery] = useState('');
  const [dialog, setDialog] = useState<'import' | 'generate' | null>(null);
  const [selected, setSelected] = useState<Sim | null>(null);
  const batches = useResource('sim-batches', loadBatches);
  const sims = useResource(`sims:${state}:${type}:${batch}`, (signal) =>
    loadSims({ state, type, batch }, signal),
  );
  const reload = () => {
    sims.retry();
    batches.retry();
  };
  return (
    <div className="bss-screen" data-ui="ui.sims.list">
      <PageHeader
        title="SIMs"
        count={sims.status === 'ok' ? sims.data.length : undefined}
        actions={
          <>
            <Button data-ui="ui.sims.import" onClick={() => setDialog('import')}>
              Import CSV
            </Button>
            <Button data-ui="ui.sims.generate" onClick={() => setDialog('generate')}>
              Generate eSIMs
            </Button>
          </>
        }
        filters={
          <>
            <TextInput
              aria-label="Search SIMs"
              placeholder="Search IMSI or ICCID"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <SelectField
              aria-label="State"
              value={state}
              onChange={(e) => setState(e.target.value as '' | SimState)}
            >
              <option value="">All states</option>
              {SIM_STATES.map((s) => (
                <option key={s} value={s}>
                  {STATE_LABEL[s]}
                </option>
              ))}
            </SelectField>
            <SelectField
              aria-label="Type"
              value={type}
              onChange={(e) => setType(e.target.value as '' | SimType)}
            >
              <option value="">All types</option>
              <option value="physical">Physical</option>
              <option value="esim">eSIM</option>
            </SelectField>
            <SelectField
              aria-label="Batch"
              value={batch}
              onChange={(e) => setBatch(e.target.value)}
            >
              <option value="">All batches</option>
              {(batches.status === 'ok' ? batches.data : []).map((b) => (
                <option key={b.name} value={b.name}>
                  {b.name} ({b.total})
                </option>
              ))}
            </SelectField>
          </>
        }
      />
      {batches.status === 'error' && (
        <ErrorBlock title="Failed to load batches" error={batches.error} onRetry={batches.retry} />
      )}
      <div role="table" aria-label="SIMs" className="bss-page-table">
        <TableHead columns={COLUMNS} />
        <ListState
          resource={sims}
          columns={COLUMNS}
          errorTitle="Failed to load SIMs"
          empty={{
            title: 'No SIMs yet',
            text: 'Import a CSV of physical SIMs or generate eSIM profiles.',
          }}
          count={(data) => data.length}
          rows={(data) =>
            data.filter(
              (s) =>
                (s.value ?? '').includes(query.trim()) ||
                (simIccid(s) ?? '').includes(query.trim()),
            )
          }
          onResetFilters={() => {
            setQuery('');
            setState('');
            setType('');
            setBatch('');
          }}
        >
          {(rows) =>
            rows.map((s) => {
              const t = simType(s);
              const st = simState(s);
              return (
                <TableRow
                  key={s.id}
                  columns={COLUMNS}
                  cells={{
                    imsi: <Button onClick={() => setSelected(s)}>{s.value}</Button>,
                    iccid: simIccid(s) ?? '—',
                    type: t ? <Tag>{TYPE_LABEL[t]}</Tag> : '—',
                    state: st ? <Tag>{STATE_LABEL[st]}</Tag> : '—',
                    batch: (s as { batchNumber?: string }).batchNumber ?? '—',
                  }}
                />
              );
            })
          }
        </ListState>
      </div>
      {dialog === 'import' && (
        <ImportSims
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            reload();
          }}
        />
      )}
      {dialog === 'generate' && (
        <GenerateEsims
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            reload();
          }}
        />
      )}
      {selected && <SimDetail sim={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function ImportSims({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState('');
  const [type, setType] = useState<SimType>('physical');
  const [file, setFile] = useState<File | undefined>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  async function submit() {
    if (!file) return;
    setBusy(true);
    setProblem('');
    try {
      const result = await importCsv(name, type, await file.text());
      toast(`Batch ${result.name} stocked: ${plural(result.total, 'SIM')}`);
      onDone();
    } catch (error) {
      // a 4xx names the bad rows and stocked nothing; anything else may have stocked
      const refused = (error as { status?: number }).status;
      const known = typeof refused === 'number' && refused >= 400 && refused < 500;
      setProblem(
        known ? explain(error) : `${explain(error)}. Check the batches before importing again.`,
      );
      setUncertain(!known);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Import SIMs"
      size="M"
      onClose={onClose}
      ui="ui.sims.importDialog"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={busy || uncertain || !BATCH_NAME.test(name) || !file}
            onClick={() => void submit()}
          >
            Import
          </Button>
        </>
      }
    >
      <Field label="Batch name">
        <TextInput value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Type">
        <SelectField
          aria-label="Type"
          value={type}
          onChange={(e) => setType(e.target.value as SimType)}
        >
          <option value="physical">Physical</option>
          <option value="esim">eSIM</option>
        </SelectField>
      </Field>
      <Field label="CSV file">
        <FileField
          label="CSV file"
          fileName={file?.name ?? null}
          onFile={setFile}
          accept=".csv,text/csv"
        />
      </Field>
      <p className="bss-caption">
        Header row <code>iccid,imsi</code>
        {type === 'esim' ? <code>,smdpAddress,matchingId</code> : null}. All or nothing, at most
        5000 rows. Ki and OPc are never accepted.
      </p>
      {problem && <Note tone="negative">{problem}</Note>}
    </Dialog>
  );
}

function GenerateEsims({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState('');
  const [count, setCount] = useState('10');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const number = Number(count);
  const valid = BATCH_NAME.test(name) && /^[0-9]+$/.test(count) && number >= 1 && number <= 5000;
  async function submit() {
    setBusy(true);
    setProblem('');
    try {
      const result = await generateEsims(name, number);
      toast(`Batch ${result.name} generated: ${plural(result.total, 'eSIM')}`);
      onDone();
    } catch (error) {
      const refused = (error as { status?: number }).status;
      const known = typeof refused === 'number' && refused >= 400 && refused < 500;
      setProblem(
        known ? explain(error) : `${explain(error)}. Check the batches before generating again.`,
      );
      setUncertain(!known);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title="Generate eSIMs"
      size="M"
      onClose={onClose}
      ui="ui.sims.generateDialog"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            data-dialog-primary
            disabled={busy || uncertain || !valid}
            onClick={() => void submit()}
          >
            Generate
          </Button>
        </>
      }
    >
      <Field label="Batch name">
        <TextInput value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Number of profiles">
        <TextInput mono value={count} onChange={(e) => setCount(e.target.value)} />
      </Field>
      <p className="bss-caption">1 to 5000 profiles, asked from the network adapter.</p>
      {problem && <Note tone="negative">{problem}</Note>}
    </Dialog>
  );
}

function SimDetail({ sim: listed, onClose }: { sim: Sim; onClose: () => void }) {
  const fresh = useResource(`sim:${listed.id}`, (signal) => loadSim(listed.id, signal));
  const sim = fresh.status === 'ok' ? fresh.data : listed;
  const products = useResource(`sim-products:${sim.id}`, (signal) =>
    loadNumberProducts(sim.id, signal),
  );
  const t = simType(sim);
  const st = simState(sim);
  return (
    <Dialog
      onSubmit={submitDialogPrimary}
      title={sim.value ?? 'SIM'}
      size="M"
      onClose={onClose}
      ui="ui.sims.detail"
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <>
        {fresh.status === 'error' && (
          <ErrorBlock title="Failed to load the SIM" error={fresh.error} onRetry={fresh.retry} />
        )}
        <KeyValue
          items={[
            ['IMSI', sim.value ?? '—'],
            ['ICCID', simIccid(sim) ?? '—'],
            ['Type', t ? TYPE_LABEL[t] : '—'],
            ['State', st ? STATE_LABEL[st] : '—'],
            ['Batch', (sim as { batchNumber?: string }).batchNumber ?? '—'],
            ['Administration', sim.administrativeState ?? '—'],
          ]}
        />
        {t === 'esim' && !(st === 'reserved' || st === 'active') && (
          <Note tone="info">
            The activation code is shown once the eSIM is assigned to a line: a stocked profile
            never exposes it.
          </Note>
        )}
        <SimActivationCode sim={sim} />
        {products.status === 'error' && (
          <ErrorBlock
            title="Failed to load assigned products"
            error={products.error}
            onRetry={products.retry}
          />
        )}
        {products.status === 'ok' &&
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
          ))}
      </>
    </Dialog>
  );
}
