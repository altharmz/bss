import type { CSSProperties, ReactNode } from 'react';

// A table built on CSS grid with fixed column widths per table (README "Table"). The
// same column list drives the header, the rows and the loading skeleton, so a skeleton is
// sized like the real cells and numeric skeletons sit on the right.

export interface Column {
  key: string;
  label: string;
  width: string; // a grid track: "150px", "1fr"
  align?: 'left' | 'right';
  mono?: boolean; // identifiers, amounts, dates, counts
}

export function gridOf(columns: Column[]): string {
  return columns.map((column) => column.width).join(' ');
}

function cellClass(column: Column): string {
  return ['cell', column.align === 'right' ? 'right' : '', column.mono ? 'mono' : '']
    .filter(Boolean)
    .join(' ');
}

export function TableHead({ columns }: { columns: Column[] }) {
  return (
    <div className="bss-row head" role="row" style={{ gridTemplateColumns: gridOf(columns) }}>
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
}: {
  columns: Column[];
  cells: Record<string, ReactNode>;
  onClick?: () => void;
  style?: CSSProperties;
}) {
  return (
    <div
      className={`bss-row${onClick ? ' clickable' : ''}`}
      role="row"
      style={{ gridTemplateColumns: gridOf(columns), ...style }}
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
