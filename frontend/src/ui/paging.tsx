import { useEffect, useState } from 'react';
import { formatInt } from '../format/money';
import { Button } from './kit';
import { TableFooter } from './Table';

// Server paging for the global lists (#312, #314): a page of rows, Previous/Next from the
// Link rel=next the server sends, and no totals. The offset belongs to the filters it was
// set for, so new filters start at the first page.

/** The search box's text, settled: the server is asked once the typing pauses. */
export function useSettled(value: string, ms = 250): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/** The page offset for `filterKey`: 0 again whenever the key changes, also back to an
 * earlier key (#466: clearing a filter returns to the first page, not the one it was on). */
export function usePageOffset(filterKey: string): [number, (offset: number) => void] {
  const [paging, setPaging] = useState({ key: filterKey, offset: 0 });
  // forget the old key's offset on the render that sees the new key (a state update during
  // render, React's pattern for state derived from a changed input)
  if (paging.key !== filterKey) setPaging({ key: filterKey, offset: 0 });
  const offset = paging.key === filterKey ? paging.offset : 0;
  return [offset, (next: number) => setPaging({ key: filterKey, offset: Math.max(0, next) })];
}

/** "Showing a–b" with Previous and Next; `ui` names the buttons (`<ui>.previous`). */
export function PageFooter({
  offset,
  shown,
  more,
  size,
  onOffset,
  ui,
  note,
}: {
  offset: number;
  shown: number;
  more: boolean;
  size: number;
  onOffset: (offset: number) => void;
  ui: string;
  note?: string;
}) {
  const range = `Showing ${formatInt(offset + 1)}–${formatInt(offset + shown)}`;
  return (
    <TableFooter
      left={note ? `${range} · ${note}` : range}
      right={
        <span className="bss-table-pager">
          <Button
            small
            data-ui={`${ui}.previous`}
            disabled={offset === 0}
            onClick={() => onOffset(offset - size)}
          >
            Previous
          </Button>
          <Button
            small
            data-ui={`${ui}.next`}
            disabled={!more}
            onClick={() => onOffset(offset + size)}
          >
            Next
          </Button>
        </span>
      }
    />
  );
}
