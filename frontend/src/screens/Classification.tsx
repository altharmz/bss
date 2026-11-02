import { submitDialogPrimary } from '../ui/kit';
import { Note } from '../ui/Note';
import { useState, type CSSProperties } from 'react';
import {
  catalogChanges,
  categoryChanges,
  categoryTree,
  classificationFields,
  createCatalog,
  createCategory,
  deleteCatalog,
  deleteCategory,
  descendantIds,
  lifecycleChoices,
  loadCatalogList,
  loadCategoryList,
  patchCatalog,
  patchCategory,
  type Category,
  type ClassificationFields,
  type ProductCatalog,
} from '../api/classification';
import type { Catalog } from '../api/bss';
import { explain } from '../api/client';
import { useResource } from '../api/useResource';
import { useSystemContext } from '../app/context';
import { formatInt } from '../format/money';
import { Button, Modal, StatusPill, TextInput, useToast } from '../ui/kit';
import { ListState } from '../ui/states';
import { TableFooter, TableHead, TableRow, type Column } from '../ui/Table';

// Catalog tabs "Categories" (a tree by parent, offering membership) and "Catalogs"
// (TMF620 productCatalog with root categories). Records only: billing never reads them.
// Design: docs/next-slices-design.md section 13.

const CATEGORY_COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'status', label: 'Status', width: '120px' },
  { key: 'offerings', label: 'Offerings', width: '100px', align: 'right', mono: true },
  { key: 'subs', label: 'Subcategories', width: '130px', align: 'right', mono: true },
];
const CATALOG_COLUMNS: Column[] = [
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'type', label: 'Type', width: '160px' },
  { key: 'status', label: 'Status', width: '120px' },
  { key: 'roots', label: 'Root categories', width: '1fr' },
];

const dash = <span className="bss-dash">—</span>;
const statusCell = (s: Category['status']) =>
  s === null ? dash : <StatusPill kind="lifecycle" status={s} />;

export function CategoriesTab({ offerings, query }: { offerings: Catalog | null; query: string }) {
  const toast = useToast();
  const [version, setVersion] = useState(0);
  const [open, setOpen] = useState<Category | 'new' | null>(null);
  const resource = useResource(`categories-tab:${version}`, loadCategoryList);
  const q = query.trim().toLowerCase();
  return (
    <div data-ui="ui.catalog.categories">
      <div className="bss-toolbar">
        <Button
          variant="primary"
          data-ui="ui.catalog.newCategory"
          disabled={resource.status !== 'ok' || offerings === null}
          onClick={() => setOpen('new')}
        >
          + New category
        </Button>
      </div>
      <div className="catalog-table">
        <TableHead columns={CATEGORY_COLUMNS} />
        <ListState
          resource={resource}
          columns={CATEGORY_COLUMNS}
          errorTitle="Failed to load the categories"
          empty={{ title: 'No categories yet', text: 'Create the first category.' }}
          total={(data) => data.length}
          rows={(data) =>
            categoryTree(data).filter((r) => !q || r.category.name.toLowerCase().includes(q))
          }
          onResetFilters={() => undefined}
        >
          {(rows, data) => (
            <>
              {rows.map(({ category, depth }) => (
                <TableRow
                  key={category.id}
                  columns={CATEGORY_COLUMNS}
                  onClick={() => setOpen(category)}
                  cells={{
                    name: (
                      <span
                        className="bss-name ellipsis category-indent"
                        style={{ '--category-indent': `${q ? 0 : depth * 20}px` } as CSSProperties}
                      >
                        {category.name}
                      </span>
                    ),
                    status: statusCell(category.status),
                    offerings: formatInt(category.offeringIds.length),
                    subs: formatInt(category.subCategoryIds.length),
                  }}
                />
              ))}
              <TableFooter
                left={`Showing ${formatInt(rows.length)} of ${formatInt(data.length)}`}
                right="Records only · billing never reads a category"
              />
            </>
          )}
        </ListState>
      </div>
      {open !== null && resource.status === 'ok' && (
        <ClassificationModal
          kind="category"
          record={open === 'new' ? null : open}
          categories={resource.data}
          offerings={offerings}
          onClose={() => setOpen(null)}
          onDone={(message) => {
            setOpen(null);
            toast(message);
            setVersion((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}

export function CatalogsTab({ query }: { query: string }) {
  const toast = useToast();
  const [version, setVersion] = useState(0);
  const [open, setOpen] = useState<ProductCatalog | 'new' | null>(null);
  const resource = useResource(`catalogs-tab:${version}`, loadCatalogList);
  const q = query.trim().toLowerCase();
  return (
    <div data-ui="ui.catalog.catalogs">
      <div className="bss-toolbar">
        <Button
          variant="primary"
          data-ui="ui.catalog.newCatalog"
          disabled={resource.status !== 'ok'}
          onClick={() => setOpen('new')}
        >
          + New catalog
        </Button>
      </div>
      <div className="catalog-table">
        <TableHead columns={CATALOG_COLUMNS} />
        <ListState
          resource={resource}
          columns={CATALOG_COLUMNS}
          errorTitle="Failed to load the catalogs"
          empty={{ title: 'No catalogs yet', text: 'Create the first product catalog.' }}
          total={(data) => data.catalogs.length}
          rows={(data) => data.catalogs.filter((c) => !q || c.name.toLowerCase().includes(q))}
          onResetFilters={() => undefined}
        >
          {(rows, data) => (
            <>
              {rows.map((c) => (
                <TableRow
                  key={c.id}
                  columns={CATALOG_COLUMNS}
                  onClick={() => setOpen(c)}
                  cells={{
                    name: <span className="bss-name ellipsis">{c.name}</span>,
                    type: c.type ?? dash,
                    status: statusCell(c.status),
                    roots:
                      c.categoryIds.length === 0 ? (
                        dash
                      ) : (
                        <span className="ellipsis">
                          {c.categoryIds
                            .map((id) => data.categories.find((x) => x.id === id)?.name ?? id)
                            .join(', ')}
                        </span>
                      ),
                  }}
                />
              ))}
              <TableFooter
                left={`Showing ${formatInt(rows.length)} of ${formatInt(data.catalogs.length)}`}
                right="Records only · billing never reads a catalog"
              />
            </>
          )}
        </ListState>
      </div>
      {open !== null && resource.status === 'ok' && (
        <ClassificationModal
          kind="catalog"
          record={open === 'new' ? null : open}
          categories={resource.data.categories}
          offerings={null}
          onClose={() => setOpen(null)}
          onDone={(message) => {
            setOpen(null);
            toast(message);
            setVersion((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}

function CheckList({
  label,
  items,
  chosen,
  onToggle,
  none,
}: {
  label: string;
  items: { id: string; name: string }[];
  chosen: string[];
  onToggle: (id: string) => void;
  none: string;
}) {
  return (
    <fieldset aria-label={label}>
      <legend>{label}</legend>
      {items.length === 0 && <Note tone="info">{none}</Note>}
      {items.map((item) => (
        <div key={item.id}>
          <label>
            <input
              type="checkbox"
              checked={chosen.includes(item.id)}
              onChange={() => onToggle(item.id)}
            />{' '}
            {item.name}
          </label>
        </div>
      ))}
    </fieldset>
  );
}

/** Create (no `record`) or edit/delete a category or a catalog. */
function ClassificationModal({
  kind,
  record,
  categories,
  offerings,
  onClose,
  onDone,
}: {
  kind: 'category' | 'catalog';
  record: Category | ProductCatalog | null;
  categories: Category[];
  offerings: Catalog | null;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const { timeZone } = useSystemContext();
  const isCategory = kind === 'category';
  const [form, setForm] = useState<ClassificationFields>(classificationFields(record, timeZone));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const set = <K extends keyof ClassificationFields>(key: K, value: ClassificationFields[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const toggle = (key: 'offeringIds' | 'categoryIds') => (id: string) =>
    setForm((f) => ({
      ...f,
      [key]: f[key].includes(id) ? f[key].filter((x) => x !== id) : [...f[key], id],
    }));

  const changes =
    record === null
      ? null
      : isCategory
        ? categoryChanges(record as Category, form, timeZone)
        : catalogChanges(record as ProductCatalog, form, timeZone);
  let error: string | null = null;
  if (form.name.trim() === '') error = 'Name is required';
  else if (form.validTo && !form.validFrom)
    error = 'A validity period needs a start date: set Valid from'; // the server refuses it
  else if (form.validFrom && form.validTo && form.validTo < form.validFrom)
    error = 'End date is before start date';
  const ready = error === null && (record === null || changes !== null);

  // the parent may be neither the category nor anything below it (no cycle)
  const banned = isCategory && record !== null ? descendantIds(categories, record.id) : new Set();
  const parents = categoryTree(categories).filter((r) => !banned.has(r.category.id));
  const roots = categories.filter((c) => c.parentId === null);
  const deletable =
    record !== null &&
    (isCategory
      ? (record as Category).subCategoryIds.length === 0 &&
        (record as Category).offeringIds.length === 0
      : true);

  async function run(action: () => Promise<string>) {
    setBusy(true);
    setProblem(null);
    try {
      onDone(await action());
    } catch (failure) {
      setProblem(explain(failure));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }
  const noun = isCategory ? 'Category' : 'Catalog';
  const save = () =>
    run(async () => {
      if (record === null) {
        if (isCategory) await createCategory(form, timeZone);
        else await createCatalog(form, timeZone);
        return `${noun} ${form.name.trim()} created`;
      }
      if (changes === null) return '';
      if (isCategory) await patchCategory(record.id, changes);
      else await patchCatalog(record.id, changes);
      return `${noun} ${form.name.trim()} saved`;
    });
  const remove = () =>
    run(async () => {
      if (record === null) return '';
      if (isCategory) await deleteCategory(record.id);
      else await deleteCatalog(record.id);
      return `${noun} ${record.name} deleted`;
    });

  return (
    <Modal
      onSubmit={submitDialogPrimary}
      title={
        record === null
          ? `New ${kind === 'category' ? 'category' : 'product catalog'}`
          : record.name
      }
      width={520}
      onClose={onClose}
      ui={`ui.catalog.${record === null ? 'new' : 'edit'}${noun}`}
      footer={
        <>
          {problem !== null && (
            <Note tone="negative" className="footer-push">
              {problem}
            </Note>
          )}
          {record !== null &&
            deletable &&
            (confirming ? (
              <span className="footer-push bss-confirm">
                <span className="ask">Delete this {kind}?</span>
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
                data-ui={`ui.catalog.delete${noun}`}
                onClick={() => setConfirming(true)}
              >
                Delete
              </Button>
            ))}
          {record !== null && !deletable && (
            <Note tone="info" className="footer-push">
              It has subcategories or offerings: remove them first to delete it.
            </Note>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready || busy} onClick={() => void save()}>
            {record === null ? 'Create' : 'Save'}
          </Button>
        </>
      }
    >
      <div className="bss-modal-body bss-form">
        <label>
          Name
          <TextInput value={form.name} onChange={(e) => set('name', e.target.value)} />
        </label>
        <label>
          Description
          <TextInput
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
          />
        </label>
        {isCategory ? (
          <label>
            Parent category
            <select
              className="bss-input"
              value={form.parentId}
              onChange={(e) => set('parentId', e.target.value)}
            >
              <option value="">None (a root)</option>
              {parents.map(({ category, depth }) => (
                <option key={category.id} value={category.id}>
                  {'– '.repeat(depth)}
                  {category.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            Catalog type
            <TextInput
              value={form.catalogType}
              onChange={(e) => set('catalogType', e.target.value)}
            />
          </label>
        )}
        <label>
          Lifecycle status
          <select
            className="bss-input"
            value={form.lifecycleStatus}
            onChange={(e) =>
              set('lifecycleStatus', e.target.value as ClassificationFields['lifecycleStatus'])
            }
          >
            {lifecycleChoices(record?.status ?? null).map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <div className="bss-form-pair">
          <label>
            Valid from
            <TextInput
              mono
              type="date"
              value={form.validFrom}
              onChange={(e) => set('validFrom', e.target.value)}
            />
          </label>
          <label>
            Valid to (empty = open-ended)
            <TextInput
              mono
              type="date"
              value={form.validTo}
              onChange={(e) => set('validTo', e.target.value)}
            />
          </label>
        </div>
        {isCategory ? (
          <CheckList
            label="Offerings"
            items={offerings?.offerings ?? []}
            chosen={form.offeringIds}
            onToggle={toggle('offeringIds')}
            none="No offerings yet."
          />
        ) : (
          <CheckList
            label="Root categories"
            items={roots}
            chosen={form.categoryIds}
            onToggle={toggle('categoryIds')}
            none="No root categories yet."
          />
        )}
        {error !== null && form.name.trim() !== '' && <Note tone="negative">{error}</Note>}
      </div>
    </Modal>
  );
}
