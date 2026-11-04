// TMF620 categories and product catalogs: the records the Catalog screen's Categories and
// Catalogs tabs show and write. Categories form a tree by `parent`; a catalog lists root
// categories; a category's `productOffering[]` is the membership and is replaced whole.
// Design: docs/ui.md.

import { addDays, businessDate } from '../format/dates';
import { getAll, patchJson, postJson, request } from './client';
import { midnightOf, lastValidDate, NEXT_LIFECYCLE, PATHS, type Lifecycle } from './bss';

const CATALOGS = '/tmf-api/productCatalogManagement/v5/productCatalog';

interface Ref {
  id?: string;
  name?: string;
}
interface ClassificationBody {
  id?: string;
  name?: string;
  description?: string;
  lifecycleStatus?: string;
  validFor?: { startDateTime?: string; endDateTime?: string };
  parent?: Ref;
  subCategory?: Ref[];
  productOffering?: Ref[];
  catalogType?: string;
  category?: Ref[];
}

function need<T>(value: T | undefined | null, what: string): T {
  if (value === undefined || value === null) throw new Error(`The server sent no ${what}`);
  return value;
}

export interface Category {
  id: string;
  name: string;
  description: string | null;
  status: Lifecycle | null;
  parentId: string | null;
  subCategoryIds: string[];
  offeringIds: string[];
  validFrom: string | null; // instant
  validTo: string | null; // instant, exclusive
}

export interface ProductCatalog {
  id: string;
  name: string;
  description: string | null;
  type: string | null;
  status: Lifecycle | null;
  categoryIds: string[];
  validFrom: string | null;
  validTo: string | null;
}

const ids = (refs: Ref[] | undefined) => (refs ?? []).flatMap((r) => (r.id ? [r.id] : []));

export function toCategory(body: ClassificationBody): Category {
  return {
    id: need(body.id, 'category id'),
    name: need(body.name, 'category name'),
    description: body.description ?? null,
    status: (body.lifecycleStatus as Lifecycle | undefined) ?? null,
    parentId: body.parent?.id ?? null,
    subCategoryIds: ids(body.subCategory),
    offeringIds: ids(body.productOffering),
    validFrom: body.validFor?.startDateTime ?? null,
    validTo: body.validFor?.endDateTime ?? null,
  };
}

export function toCatalog(body: ClassificationBody): ProductCatalog {
  return {
    id: need(body.id, 'catalog id'),
    name: need(body.name, 'catalog name'),
    description: body.description ?? null,
    type: body.catalogType ?? null,
    status: (body.lifecycleStatus as Lifecycle | undefined) ?? null,
    categoryIds: ids(body.category),
    validFrom: body.validFor?.startDateTime ?? null,
    validTo: body.validFor?.endDateTime ?? null,
  };
}

export async function loadCategoryList(signal: AbortSignal): Promise<Category[]> {
  return (await getAll<ClassificationBody>(PATHS.categories, signal)).map(toCategory);
}

export interface CatalogList {
  catalogs: ProductCatalog[];
  categories: Category[];
}

export async function loadCatalogList(signal: AbortSignal): Promise<CatalogList> {
  const [catalogs, categories] = await Promise.all([
    getAll<ClassificationBody>(CATALOGS, signal),
    loadCategoryList(signal),
  ]);
  return { catalogs: catalogs.map(toCatalog), categories };
}

export interface TreeRow {
  category: Category;
  depth: number;
}

/** Roots first, each followed by its subcategories (by name), depth-first. */
export function categoryTree(categories: Category[]): TreeRow[] {
  const byName = (a: Category, b: Category) => a.name.localeCompare(b.name);
  const known = new Set(categories.map((c) => c.id));
  const children = new Map<string | null, Category[]>();
  for (const c of categories) {
    // a parent that is not in the list cannot happen; it is shown as a root, not hidden
    const key = c.parentId !== null && known.has(c.parentId) ? c.parentId : null;
    children.set(key, [...(children.get(key) ?? []), c]);
  }
  const rows: TreeRow[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const c of (children.get(parent) ?? []).sort(byName)) {
      rows.push({ category: c, depth });
      walk(c.id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

/** The category and everything below it: none of these may become its parent. */
export function descendantIds(categories: Category[], id: string): Set<string> {
  const found = new Set<string>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const c of categories)
      if (c.parentId !== null && found.has(c.parentId) && !found.has(c.id)) {
        found.add(c.id);
        grew = true;
      }
  }
  return found;
}

export interface ClassificationFields {
  name: string;
  description: string;
  lifecycleStatus: Lifecycle;
  validFrom: string; // business date, '' = none
  validTo: string; // business date, last valid day, '' = open-ended
  parentId: string; // categories only, '' = a root
  offeringIds: string[]; // categories only
  catalogType: string; // catalogs only
  categoryIds: string[]; // catalogs only (root categories)
}

export function classificationFields(
  was: Category | ProductCatalog | null,
  timeZone: string,
): ClassificationFields {
  const category = was !== null && 'parentId' in was ? was : null;
  const catalog = was !== null && 'categoryIds' in was ? was : null;
  return {
    name: was?.name ?? '',
    description: was?.description ?? '',
    lifecycleStatus: was?.status ?? 'In Design',
    validFrom: was?.validFrom ? businessDate(was.validFrom, timeZone) : '',
    validTo: was?.validTo ? lastValidDate(was.validTo, timeZone) : '',
    parentId: category?.parentId ?? '',
    offeringIds: category?.offeringIds ?? [],
    catalogType: catalog?.type ?? '',
    categoryIds: catalog?.categoryIds ?? [],
  };
}

function validFor(form: ClassificationFields, timeZone: string, keepStart: string | null) {
  return {
    ...(form.validFrom === ''
      ? {}
      : { startDateTime: keepStart ?? midnightOf(form.validFrom, timeZone) }),
    ...(form.validTo === '' ? {} : { endDateTime: midnightOf(addDays(form.validTo, 1), timeZone) }),
  };
}

const sameSet = (a: string[], b: string[]) => [...a].sort().join() === [...b].sort().join();
const refs = (list: string[], type: string) => list.map((id) => ({ id, '@type': type }));

function commonChanges(
  was: Category | ProductCatalog,
  form: ClassificationFields,
  timeZone: string,
): Record<string, unknown> {
  const before = classificationFields(was, timeZone);
  const changes: Record<string, unknown> = {};
  if (form.name.trim() !== before.name) changes.name = form.name.trim();
  if (form.description.trim() !== before.description)
    changes.description = form.description.trim() === '' ? null : form.description.trim();
  if (form.lifecycleStatus !== was.status) changes.lifecycleStatus = form.lifecycleStatus;
  if (form.validFrom !== before.validFrom || form.validTo !== before.validTo)
    changes.validFor =
      form.validFrom === '' && form.validTo === ''
        ? null
        : // an unchanged start is sent as loaded: recomputing it could "move" it
          validFor(form, timeZone, form.validFrom === before.validFrom ? was.validFrom : null);
  return changes;
}

function commonBody(type: string, form: ClassificationFields, timeZone: string) {
  const body: Record<string, unknown> = {
    '@type': type,
    name: form.name.trim(),
    lifecycleStatus: form.lifecycleStatus,
  };
  if (form.description.trim()) body.description = form.description.trim();
  if (form.validFrom !== '' || form.validTo !== '') body.validFor = validFor(form, timeZone, null);
  return body;
}

/** Only the changed fields; null = nothing changed. `productOffering` is the whole
 * membership (the API replaces the array). */
export function categoryChanges(
  was: Category,
  form: ClassificationFields,
  timeZone: string,
): Record<string, unknown> | null {
  const changes = commonChanges(was, form, timeZone);
  if (form.parentId !== (was.parentId ?? ''))
    changes.parent = form.parentId === '' ? null : { id: form.parentId, '@type': 'CategoryRef' };
  if (!sameSet(form.offeringIds, was.offeringIds))
    changes.productOffering = refs(form.offeringIds, 'ProductOfferingRef');
  return Object.keys(changes).length === 0 ? null : changes;
}

export function catalogChanges(
  was: ProductCatalog,
  form: ClassificationFields,
  timeZone: string,
): Record<string, unknown> | null {
  const changes = commonChanges(was, form, timeZone);
  if (form.catalogType.trim() !== (was.type ?? ''))
    changes.catalogType = form.catalogType.trim() === '' ? null : form.catalogType.trim();
  if (!sameSet(form.categoryIds, was.categoryIds))
    changes.category = refs(form.categoryIds, 'CategoryRef');
  return Object.keys(changes).length === 0 ? null : changes;
}

export async function createCategory(
  form: ClassificationFields,
  timeZone: string,
): Promise<Category> {
  const body = commonBody('Category', form, timeZone);
  if (form.parentId !== '') body.parent = { id: form.parentId, '@type': 'CategoryRef' };
  if (form.offeringIds.length > 0)
    body.productOffering = refs(form.offeringIds, 'ProductOfferingRef');
  return toCategory(await postJson<ClassificationBody>(PATHS.categories, body));
}

export async function createCatalog(
  form: ClassificationFields,
  timeZone: string,
): Promise<ProductCatalog> {
  const body = commonBody('ProductCatalog', form, timeZone);
  if (form.catalogType.trim()) body.catalogType = form.catalogType.trim();
  if (form.categoryIds.length > 0) body.category = refs(form.categoryIds, 'CategoryRef');
  return toCatalog(await postJson<ClassificationBody>(CATALOGS, body));
}

export async function patchCategory(id: string, changes: Record<string, unknown>) {
  await patchJson(`${PATHS.categories}/${id}`, changes);
}
export async function patchCatalog(id: string, changes: Record<string, unknown>) {
  await patchJson(`${CATALOGS}/${id}`, changes);
}
export async function deleteCategory(id: string) {
  await request('DELETE', `${PATHS.categories}/${id}`);
}
export async function deleteCatalog(id: string) {
  await request('DELETE', `${CATALOGS}/${id}`);
}

/** The lifecycle choices: the current status and the next forward one. */
export function lifecycleChoices(status: Lifecycle | null): Lifecycle[] {
  if (status === null) return ['In Design', 'Active', 'Retired'];
  const next = NEXT_LIFECYCLE[status];
  return next === null ? [status] : [status, next];
}
