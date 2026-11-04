import type { CSSProperties, ReactNode } from 'react';
import { Menu, type MenuItem } from './navigation';

// A table built on CSS grid with fixed column widths per table (README "Table"). The
// same column list drives the header, the rows and the loading skeleton, so a skeleton is
// sized like the real cells and numeric skeletons sit on the right.

export interface Column {
  key: string;
  label: string;
  width: string; // a grid track: "150px", "1fr"
  align?: 'left' | 'right';
  kind?: 'actions';
  mono?: boolean; // identifiers, amounts, dates, counts
}

export function gridOf(columns: Column[]): string {
  return columns.map((column) => column.width).join(' ');
}

/** Dates and periods never wrap (B17). */
const NOWRAP_KEYS = new Set([
  'date',
  'period',
  'due',
  'created',
  'updated',
  'since',
  'from',
  'activated',
]);

function cellClass(column: Column): string {
  return [
    'cell',
    column.align === 'right' ? 'right' : '',
    column.mono ? 'mono' : '',
    column.key === 'name' ? 'name' : '',
    NOWRAP_KEYS.has(column.key) ? 'nowrap' : '',
    column.kind === 'actions' ? 'actions' : '',
  ]
    .filter(Boolean)
    .join(' ');
}

type Density = 'default' | 'compact' | 'dense';

export function TableHead({
  columns,
  density = 'default',
}: {
  columns: Column[];
  density?: Density;
}) {
  return (
    <div
      className={`bss-row head density-${density}`}
      role="row"
      style={{ '--table-columns': gridOf(columns) } as CSSProperties}
    >
      {columns.map((column) => (
        <div key={column.key} className={cellClass(column)} role="columnheader">
          {column.label}
        </div>
      ))}
    </div>
  );
}

export function TableRow({
  columns,
  cells,
  onClick,
  onActivate,
  style,
  className = '',
  density = 'default',
  selected = false,
  actions,
}: {
  columns: Column[];
  cells: Record<string, ReactNode>;
  onClick?: () => void;
  /** Enter, Space or a double click; defaults to onClick. */
  onActivate?: () => void;
  style?: CSSProperties;
  className?: string;
  density?: Density;
  selected?: boolean;
  actions?: MenuItem[];
}) {
  return (
    <div
      className={`bss-row density-${density}${onClick ? ' clickable' : ''}${selected ? ' selected' : ''} ${className}`.trim()}
      role="row"
      style={{ '--table-columns': gridOf(columns), ...style } as CSSProperties}
      aria-selected={selected || undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={(event) => {
        if (
          event.target === event.currentTarget &&
          onClick &&
          (event.key === 'Enter' || event.key === ' ')
        ) {
          event.preventDefault();
          (onActivate ?? onClick)();
        }
      }}
      onClick={onClick}
      {...(onActivate ? { onDoubleClick: onActivate } : {})}
    >
      {columns.map((column) => (
        <div
          key={column.key}
          className={cellClass(column)}
          role="cell"
          {...(column.key === 'name' && typeof cells[column.key] === 'string'
            ? { title: cells[column.key] as string }
            : {})}
        >
          {column.kind === 'actions' && actions ? <Menu items={actions} /> : cells[column.key]}
        </div>
      ))}
    </div>
  );
}

export function TableFooter({
  left,
  right,
  range,
}: {
  left?: ReactNode;
  right?: ReactNode;
  range?: { from: number; to: number; total: number };
}) {
  return (
    <div className="bss-table-footer">
      <span>
        {range ? (
          <>
            <span className="range">
              {range.from.toLocaleString('en-US')}–{range.to.toLocaleString('en-US')}
            </span>{' '}
            of {range.total.toLocaleString('en-US')}
          </>
        ) : (
          left
        )}
      </span>
      {right !== undefined && <span>{right}</span>}
    </div>
  );
}
