import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useState } from 'react';
import {
  checkDocument,
  createExportJob,
  createImportJob,
  deleteJob,
  downloadBlob,
  downloadName,
  fromDataUrl,
  loadCatalogChoices,
  loadJobs,
  saveBlob,
  type CatalogJob,
} from '../api/catalogJobs';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { businessDate, clockTime } from '../format/dates';
import { formatInt } from '../format/money';
import { Button, Modal, StatusPill, useToast } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';

// Catalog tab "Import / export": TMF620 importJob and exportJob. Both run inside their
// request, so there is no polling. Design: docs/ui.md.

const COLUMNS: Column[] = [
  { key: 'kind', label: 'Kind', width: '100px' },
  { key: 'status', label: 'Status', width: '120px' },
  { key: 'created', label: 'Created', width: '180px', mono: true },
  { key: 'completed', label: 'Completed', width: '180px', mono: true },
  { key: 'error', label: 'Error', width: '1fr' },
];

const dash = <span className="bss-dash">—</span>;
const firstLine = (text: string | null) => (text === null ? '' : (text.split('\n')[0] ?? ''));

function stamp(instant: string | null, timeZone: string) {
  return instant === null
    ? dash
    : `${businessDate(instant, timeZone)} ${clockTime(instant, timeZone)}`;
}

type Dialog =
  { kind: 'export' } | { kind: 'import'; text: string } | { kind: 'job'; job: CatalogJob };

export function JobsTab() {
  const toast = useToast();
  const { timeZone } = useSystemContext();
  const [version, setVersion] = useState(0);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [dirty, setDirty] = useState(false);
  const resource = useResource(`catalog-jobs:${version}`, loadJobs);
  const close = () => {
    setDialog(null);
    if (dirty) {
      setDirty(false);
      setVersion((v) => v + 1);
    }
  };
  return (
    <div data-ui="ui.catalog.jobs">
      <div className="bss-toolbar">
        <Button
          variant="primary"
          data-ui="ui.catalog.export"
          onClick={() => setDialog({ kind: 'export' })}
        >
          Export...
        </Button>
        <Button data-ui="ui.catalog.import" onClick={() => setDialog({ kind: 'import', text: '' })}>
          Import...
        </Button>
      </div>
      <div className="catalog-table">
        <TableHead columns={COLUMNS} />
        <ListState
          resource={resource}
          columns={COLUMNS}
          errorTitle="Failed to load the import and export jobs"
          empty={{ title: 'No jobs yet', text: 'Export the catalog or import a document.' }}
          total={(data) => data.length}
          rows={(data) => data}
          onResetFilters={() => undefined}
        >
          {(rows) => (
            <>
              {rows.map((job) => (
                <TableRow
                  key={`${job.kind}:${job.id}`}
                  columns={COLUMNS}
                  onClick={() => setDialog({ kind: 'job', job })}
                  cells={{
                    kind: job.kind === 'ImportJob' ? 'Import' : 'Export',
                    status: <StatusPill kind="catalogJob" status={job.status} />,
                    created: stamp(job.created, timeZone),
                    completed: stamp(job.completed, timeZone),
                    error:
                      job.errorLog === null ? (
                        dash
                      ) : (
                        <span className="ellipsis">{firstLine(job.errorLog)}</span>
                      ),
                  }}
                />
              ))}
              <TableFooter
                left={`${formatInt(rows.length)} jobs`}
                right="A job is a record; deleting it undoes nothing"
              />
            </>
          )}
        </ListState>
      </div>
      {dialog?.kind === 'export' && (
        <ExportModal
          onCreated={() => setDirty(true)}
          onClose={close}
          onCopy={(text) => {
            setDialog({ kind: 'import', text });
          }}
        />
      )}
      {dialog?.kind === 'import' && (
        <ImportModal
          initial={dialog.text}
          onCreated={() => setDirty(true)}
          onClose={close}
          onDone={(message) => {
            toast(message);
            close();
          }}
        />
      )}
      {dialog?.kind === 'job' && (
        <JobModal
          job={dialog.job}
          onClose={close}
          onDeleted={() => {
            toast(`${dialog.job.kind === 'ImportJob' ? 'Import' : 'Export'} job deleted`);
            setDialog(null);
            setDirty(false);
            setVersion((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}

function download(job: CatalogJob, timeZone: string) {
  const blob = downloadBlob(job);
  if (blob === null) return;
  saveBlob(blob, downloadName(businessDate(job.created ?? '', timeZone)));
}

function ErrorLog({ text }: { text: string }) {
  return (
    <Note tone="negative" className="job-result" data-ui="ui.catalog.errorLog">
      <pre>{text}</pre>
    </Note>
  );
}

function ExportModal({
  onCreated,
  onClose,
  onCopy,
}: {
  onCreated: () => void;
  onClose: () => void;
  onCopy: (text: string) => void;
}) {
  const { timeZone } = useSystemContext();
  const catalogs = useResource('catalog-choices', loadCatalogChoices);
  const [scope, setScope] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [job, setJob] = useState<CatalogJob | null>(null);

  async function run() {
    setBusy(true);
    setProblem(null);
    try {
      setJob(await createExportJob(scope));
      onCreated();
    } catch (failure) {
      setProblem(explain(failure));
    } finally {
      setBusy(false);
    }
  }
  const document = job?.status === 'Succeeded' && job.url !== null ? fromDataUrl(job.url) : null;
  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title="Export the catalog"
      width={480}
      onClose={onClose}
      ui="ui.catalog.export"
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          <Button onClick={onClose}>{job === null ? 'Cancel' : 'Close'}</Button>
          {job === null && (
            <Button
              variant="primary"
              disabled={busy || catalogs.status !== 'ok'}
              onClick={() => void run()}
            >
              Export
            </Button>
          )}
          {document !== null && job !== null && (
            <>
              <Button onClick={() => onCopy(document)}>Copy as import</Button>
              <Button variant="primary" onClick={() => download(job, timeZone)}>
                Download
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <label>
          Scope
          <select
            className="bss-input"
            value={scope}
            disabled={job !== null}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="">Everything</option>
            {(catalogs.status === 'ok' ? catalogs.data : []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {job !== null && (
          <Note tone="info">
            Export job {job.status}
            {document !== null && ': download the document or copy it into a new import.'}
          </Note>
        )}
        {job?.errorLog != null && <ErrorLog text={job.errorLog} />}
      </div>
    </Modal>
  );
}

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('The file could not be read'));
    reader.readAsText(file);
  });
}

function ImportModal({
  initial,
  onCreated,
  onClose,
  onDone,
}: {
  initial: string;
  onCreated: () => void;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const check = text === '' ? null : checkDocument(text);

  async function choose(file: File | undefined) {
    if (file === undefined) return;
    setProblem(null);
    try {
      setText(await readText(file));
    } catch (failure) {
      setProblem(explain(failure));
    }
  }
  async function run() {
    setBusy(true);
    setProblem(null);
    setFailed(null);
    try {
      const job = await createImportJob(text);
      onCreated();
      if (job.status === 'Succeeded') onDone('Import job succeeded');
      else setFailed(job.errorLog ?? `Import job ${job.status}`);
    } catch (failure) {
      setProblem(explain(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title="Import a catalog document"
      width={560}
      onClose={onClose}
      ui="ui.catalog.import"
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={busy || text === '' || checkDocument(text) !== null}
            onClick={() => void run()}
          >
            Import
          </Button>
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <label>
          JSON file
          <input
            type="file"
            accept=".json,application/json"
            onChange={(e) => void choose(e.target.files?.[0])}
          />
        </label>
        <label>
          Or paste the document
          <textarea
            className="bss-input job-source"
            rows={10}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        {check !== null && <Note tone="negative">{check}</Note>}
        <Note tone="info">
          Everything is imported or nothing is. A document already imported is skipped.
        </Note>
        {failed !== null && <ErrorLog text={failed} />}
      </div>
    </Modal>
  );
}

function JobModal({
  job,
  onClose,
  onDeleted,
}: {
  job: CatalogJob;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { timeZone } = useSystemContext();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const isImport = job.kind === 'ImportJob';
  async function remove() {
    setBusy(true);
    try {
      await deleteJob(job);
      onDeleted();
    } catch (failure) {
      setProblem(explain(failure));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title={isImport ? 'Import job' : 'Export job'}
      subtitle={job.status}
      width={520}
      onClose={onClose}
      ui="ui.catalog.job"
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          {confirming ? (
            <span className="footer-push bss-confirm">
              <span className="ask">
                {isImport
                  ? 'Delete this job record? Nothing it imported is undone.'
                  : 'Delete this job record?'}
              </span>
              <Button variant="danger" small disabled={busy} onClick={() => void remove()}>
                Yes
              </Button>
              <Button small disabled={busy} onClick={() => setConfirming(false)}>
                No
              </Button>
            </span>
          ) : (
            <Button
              variant="quiet"
              tone="negative"
              data-ui="ui.catalog.deleteJob"
              onClick={() => setConfirming(true)}
            >
              Delete
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
          {!isImport && job.status === 'Succeeded' && job.url !== null && (
            <Button variant="primary" onClick={() => download(job, timeZone)}>
              Download
            </Button>
          )}
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <span>Created {stamp(job.created, timeZone)}</span>
        <span>Completed {stamp(job.completed, timeZone)}</span>
        {job.query !== null && <span>Scope {job.query}</span>}
        {job.errorLog !== null && <ErrorLog text={job.errorLog} />}
      </div>
    </Modal>
  );
}
