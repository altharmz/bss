import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import type { ResourceWithRetry } from '../api/useResource';
import { Button } from './kit';
import { TableRow, type Column } from './Table';

// The four required states of every list (README "Required states"): loading skeleton,
// error with the failing call and Retry, empty with an explanation and an optional
// action, and "No matches" with Reset filters when filters hide every row.

// Bar widths cycle per row, as in the design; numeric columns keep them right-aligned.
const WIDTHS = ['62%', '48%', '70%', '40%', '55%', '66%', '44%', '58%'];
export const SKELETON_ROWS = 8;

export function useDelayed(ms: number) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setReady(true), ms);
    return () => clearTimeout(timer);
  }, [ms]);
  return ready;
}

export function SkeletonRows({
  columns,
  rows = SKELETON_ROWS,
}: {
  columns: Column[];
  rows?: number;
}) {
  const ready = useDelayed(200);
  return (
    <div
      aria-busy="true"
      aria-label="Loading"
      className={ready ? 'skeleton-ready' : 'skeleton-pending'}
    >
      {Array.from({ length: rows }, (_, row) => (
        <TableRow
          key={row}
          columns={columns}
          cells={Object.fromEntries(
            columns.map((column, i) => [
              column.key,
              <span
                key={column.key}
                className="bss-skel"
                data-testid="skeleton"
                style={{ '--skeleton-width': WIDTHS[(row + i) % WIDTHS.length] } as CSSProperties}
              />,
            ]),
          )}
        />
      ))}
    </div>
  );
}

export function ErrorBlock({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: Error;
  onRetry: () => void;
}) {
  return (
    <div className="bss-state" role="alert">
      <span className="icon" aria-hidden="true">
        !
      </span>
      <div className="title">{title}</div>
      {/* An ApiError's message is the call: "GET /path → 503 Service Unavailable". */}
      <div className="call">{error.message}</div>
      <Button onClick={onRetry}>Retry</Button>
    </div>
  );
}

export interface EmptyCopy {
  title: string;
  text: string;
  action?: ReactNode;
}

export function EmptyBlock({ title, text, action }: EmptyCopy) {
  return (
    <div className="bss-state">
      <div className="title">{title}</div>
      <div className="text">{text}</div>
      {action}
    </div>
  );
}

interface FilterCopy {
  query?: string | undefined;
  filters?: { label: string; value: string }[] | undefined;
}

export function NoResults({ onReset, query, filters = [] }: FilterCopy & { onReset: () => void }) {
  return (
    <div className="bss-state">
      <div className="title">
        {query === undefined ? 'No matches' : `Nothing matches “${query}”`}
      </div>
      {filters.map(({ label, value }) => (
        <div className="text" key={label}>
          with {label} <strong>{value}</strong>
        </div>
      ))}
      <Button onClick={onReset}>Reset filters</Button>
    </div>
  );
}

/**
 * Picks the state of a list. `rows` maps the loaded data to the rows the filters let
 * through; `total` is the number before filtering. Every list renders through this.
 */
export function ListState<T, R>({
  resource,
  columns,
  errorTitle,
  empty,
  total,
  rows,
  onResetFilters,
  query,
  filters,
  children,
}: {
  query?: string | undefined;
  filters?: FilterCopy['filters'];
  resource: ResourceWithRetry<T>;
  columns: Column[];
  errorTitle: string;
  empty: EmptyCopy;
  total: (data: T) => number;
  rows: (data: T) => R[];
  onResetFilters: () => void;
  children: (rows: R[], data: T) => ReactNode;
}) {
  if (resource.status === 'loading') return <SkeletonRows columns={columns} />;
  if (resource.status === 'error') {
    return <ErrorBlock title={errorTitle} error={resource.error} onRetry={resource.retry} />;
  }
  if (total(resource.data) === 0) return <EmptyBlock {...empty} />;
  const visible = rows(resource.data);
  if (visible.length === 0)
    return <NoResults onReset={onResetFilters} query={query} filters={filters} />;
  return <>{children(visible, resource.data)}</>;
}
