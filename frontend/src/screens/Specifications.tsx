import { Note } from '../ui/Note';
import { useState } from 'react';
import {
  createSpecification,
  deleteSpecification,
  NEXT_LIFECYCLE,
  patchSpecification,
  specificationChanges,
  type Catalog,
  type Lifecycle,
  type Specification,
  type SpecificationFields,
} from '../api/bss';
import { explain } from '../api/client';
import { useSystemContext } from '../app/context';
import { businessDate } from '../format/dates';
import { formatInt } from '../format/money';
import { Button, Modal, Panel, StatusPill, TextInput } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';
import type { ResourceWithRetry } from '../api/useResource';

// Catalog tab "Specifications": TMF620 productSpecification records. Characteristics and
// the other documents stay API/MCP; billing never reads a specification. Design:
// docs/next-slices-design.md section 11.

const COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'status', label: 'Status', width: '120px' },
  { key: 'brand', label: 'Brand', width: '160px' },
  { key: 'version', label: 'Version', width: '100px', mono: true },
  { key: 'offerings', label: 'Offerings', width: '100px', align: 'right', mono: true },
  { key: 'updated', label: 'Updated', width: '120px', mono: true },
];

const ALL: Lifecycle[] = ['In Design', 'Active', 'Retired'];

export function usedBy(catalog: Catalog, spec: Specification): number {
  return catalog.offerings.filter((o) => o.specificationId === spec.id).length;
}

export function SpecificationsTab({
  resource,
  query,
  onChanged,
}: {
  resource: ResourceWithRetry<Catalog>;
  query: string;
  onChanged: (message: string) => void;
}) {
  const context = useSystemContext();
  const [open, setOpen] = useState<Specification | null>(null);
  const q = query.trim().toLowerCase();
  return (
    <>
      <Panel>
        <TableHead columns={COLUMNS} />
        <ListState
          resource={resource}
          columns={COLUMNS}
          errorTitle="Failed to load the specifications"
          empty={{
            title: 'No specifications yet',
            text: 'Create the first product specification.',
          }}
          total={(data) => data.specifications.length}
          rows={(data) =>
            data.specifications.filter(
              (s) =>
                !q || s.name.toLowerCase().includes(q) || (s.brand ?? '').toLowerCase().includes(q),
            )
          }
          onResetFilters={() => undefined}
        >
          {(rows, data) => (
            <>
              {rows.map((spec) => (
                <TableRow
                  key={spec.id}
                  columns={COLUMNS}
                  onClick={() => setOpen(spec)}
                  cells={{
                    name: <span className="bss-name ellipsis">{spec.name}</span>,
                    status:
                      spec.status === null ? (
                        <span className="bss-dash">—</span>
                      ) : (
                        <StatusPill kind="lifecycle" status={spec.status} />
                      ),
                    brand: spec.brand ?? <span className="bss-dash">—</span>,
                    version: spec.version ?? <span className="bss-dash">—</span>,
                    offerings: formatInt(usedBy(data, spec)),
                    updated:
                      spec.updated === null ? (
                        <span className="bss-dash">—</span>
                      ) : (
                        businessDate(spec.updated, context.timeZone)
                      ),
                  }}
                />
              ))}
              <TableFooter
                left={`Showing ${formatInt(rows.length)} of ${formatInt(data.specifications.length)}`}
                right="Records only · billing never reads a specification"
              />
            </>
          )}
        </ListState>
      </Panel>
      {open !== null && resource.status === 'ok' && (
        <SpecificationModal
          spec={open}
          used={usedBy(resource.data, open)}
          onClose={() => setOpen(null)}
          onDone={(message) => {
            setOpen(null);
            onChanged(message);
          }}
        />
      )}
    </>
  );
}

function fieldsOf(spec: Specification | null): SpecificationFields {
  return {
    name: spec?.name ?? '',
    description: spec?.description ?? '',
    brand: spec?.brand ?? '',
    version: spec?.version ?? '',
    productNumber: spec?.productNumber ?? '',
    lifecycleStatus: spec?.status ?? 'In Design',
  };
}

/** Create (no `spec`) or edit/delete one specification. */
export function SpecificationModal({
  spec = null,
  used = 0,
  onClose,
  onCreated,
  onDone,
}: {
  spec?: Specification | null;
  used?: number;
  onClose: () => void;
  onCreated?: (created: Specification) => void;
  onDone?: (message: string) => void;
}) {
  const [form, setForm] = useState<SpecificationFields>(fieldsOf(spec));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const set = (key: keyof SpecificationFields) => (value: string) =>
    setForm((f) => ({ ...f, [key]: value }));

  const next = spec?.status ? NEXT_LIFECYCLE[spec.status] : null;
  const lifecycles: Lifecycle[] =
    spec === null
      ? ALL
      : spec.status === null
        ? ALL
        : next === null
          ? [spec.status]
          : [spec.status, next];
  const changes = spec === null ? null : specificationChanges(spec, form);
  const ready = form.name.trim() !== '' && (spec === null || changes !== null);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setProblem(null);
    try {
      await action();
    } catch (error) {
      setProblem(explain(error));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  const save = () =>
    run(async () => {
      if (spec === null) {
        onCreated?.(await createSpecification(form));
      } else if (changes !== null) {
        await patchSpecification(spec.id, changes);
        onDone?.(`Specification ${form.name.trim()} saved`);
      }
    });
  const remove = () =>
    run(async () => {
      if (spec === null) return;
      await deleteSpecification(spec.id);
      onDone?.(`Specification ${spec.name} deleted`);
    });

  return (
    <Modal
      title={spec === null ? 'New product specification' : spec.name}
      width={520}
      onClose={onClose}
      ui={spec === null ? 'ui.catalog.newSpecification' : 'ui.catalog.editSpecification'}
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          {spec !== null &&
            used === 0 &&
            (confirming ? (
              <span className="footer-push bss-confirm">
                <span className="ask">Delete this specification?</span>
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
                data-ui="ui.catalog.deleteSpecification"
                onClick={() => setConfirming(true)}
              >
                Delete
              </Button>
            ))}
          {spec !== null && used > 0 && (
            <Note tone="info" className="footer-push">
              Used by {used} offering{used === 1 ? '' : 's'}: it cannot be deleted.
            </Note>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready || busy} onClick={() => void save()}>
            {spec === null ? 'Create' : 'Save'}
          </Button>
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <label>
          Name
          <TextInput value={form.name} onChange={(e) => set('name')(e.target.value)} />
        </label>
        <label>
          Description
          <TextInput
            value={form.description}
            onChange={(e) => set('description')(e.target.value)}
          />
        </label>
        <div className="bss-form-pair">
          <label>
            Brand
            <TextInput value={form.brand} onChange={(e) => set('brand')(e.target.value)} />
          </label>
          <label>
            Version
            <TextInput mono value={form.version} onChange={(e) => set('version')(e.target.value)} />
          </label>
        </div>
        <label>
          Product number
          <TextInput
            mono
            value={form.productNumber}
            onChange={(e) => set('productNumber')(e.target.value)}
          />
        </label>
        <label>
          Lifecycle status
          <select
            className="bss-input"
            value={form.lifecycleStatus}
            onChange={(e) => set('lifecycleStatus')(e.target.value)}
          >
            {lifecycles.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>
    </Modal>
  );
}
