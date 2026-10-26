import type { CSSProperties, ReactNode } from 'react';

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

function cellClass(column: Column): string {
  return [
    'cell',
    column.align === 'right' ? 'right' : '',
    column.mono ? 'mono' : '',
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
  style,
  className = '',
  density = 'default',
  selected = false,
}: {
  columns: Column[];
  cells: Record<string, ReactNode>;
  onClick?: () => void;
  style?: CSSProperties;
  className?: string;
  density?: Density;
  selected?: boolean;
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
          onClick();
        }
      }}
      onClick={onClick}
    >
      {columns.map((column) => (
        <div key={column.key} className={cellClass(column)} role="cell">
          {cells[column.key]}
        </div>
      ))}
    </div>
  );
}

export function TableFooter({ left, right }: { left: ReactNode; right?: ReactNode }) {
  return (
    <div className="bss-table-footer">
      <span>{left}</span>
      {right !== undefined && <span>{right}</span>}
    </div>
  );
}
