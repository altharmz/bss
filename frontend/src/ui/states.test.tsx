import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { SystemContextProvider } from '../app/context';
import { CONTEXT } from '../test/fetch';
import type { ResourceWithRetry } from '../api/useResource';
import { Button } from './kit';
import { ErrorBlock, ListState, NoResults, SKELETON_ROWS, SkeletonRows } from './states';
import type { Column } from './Table';

const COLUMNS: Column[] = [
  { key: 'no', label: 'Subscriber no.', width: '150px', mono: true },
  { key: 'name', label: 'Name', width: '1fr' },
  { key: 'balance', label: 'Balance', width: '190px', align: 'right', mono: true },
];

type Data = { names: string[] };

function resource(value: Partial<ResourceWithRetry<Data>>): ResourceWithRetry<Data> {
  return { status: 'loading', retry: vi.fn(), ...value } as ResourceWithRetry<Data>;
}

function renderList(res: ResourceWithRetry<Data>, filter = '', onReset = vi.fn()) {
  render(
    <ListState
      resource={res}
      columns={COLUMNS}
      errorTitle="Failed to load subscribers"
      empty={{
        title: 'No subscribers yet',
        text: 'Subscribers appear after registration via CRM or import.',
        action: <Button>+ Add</Button>,
      }}
      count={(data) => data.names.length}
      rows={(data) => data.names.filter((n) => n.includes(filter))}
      onResetFilters={onReset}
    >
      {(rows) => (
        <ul>
          {rows.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </ListState>,
  );
  return { onReset };
}

afterEach(() => vi.useRealTimers());

describe('loading', () => {
  it('shows 8 skeleton rows sized like the real cells', () => {
    renderList(resource({ status: 'loading' }));
    const rows = within(screen.getByLabelText('Loading')).getAllByRole('row');
    expect(rows).toHaveLength(SKELETON_ROWS);
    for (const row of rows) {
      expect(row.style.getPropertyValue('--table-columns')).toBe('150px 1fr 190px');
      const cells = within(row).getAllByRole('cell');
      expect(cells).toHaveLength(3);
      // numeric columns keep their bars right-aligned, mono like the real values
      expect(cells[2]?.className).toContain('right');
      expect(cells[0]?.className).toContain('mono');
    }
    expect(screen.getAllByTestId('skeleton')).toHaveLength(SKELETON_ROWS * 3);
  });

  it('reserves row height immediately but reveals bars only after 200 ms', () => {
    vi.useFakeTimers();
    render(<SkeletonRows columns={COLUMNS} rows={2} />);
    const loading = screen.getByLabelText('Loading');
    expect(within(loading).getAllByRole('row')).toHaveLength(2);
    expect(loading.className).toBe('skeleton-pending');
    act(() => vi.advanceTimersByTime(199));
    expect(loading.className).toBe('skeleton-pending');
    act(() => vi.advanceTimersByTime(1));
    expect(loading.className).toBe('skeleton-ready');
  });

  it('varies the bar widths per row', () => {
    render(<SkeletonRows columns={COLUMNS} rows={2} />);
    const bars = screen
      .getAllByTestId('skeleton')
      .map((bar) => bar.style.getPropertyValue('--skeleton-width'));
    expect(bars.slice(0, 3)).not.toEqual(bars.slice(3, 6));
  });
});

describe('error', () => {
  it('captures each failure time in the business zone and keeps it across rerenders', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-26T12:34:56Z'));
    const error = new Error('GET /example → 503');
    const show = (failure: Error) => (
      <SystemContextProvider value={CONTEXT}>
        <ErrorBlock title="Failed" error={failure} onRetry={() => {}} />
      </SystemContextProvider>
    );
    const view = render(show(error));
    expect(screen.getByText('15:34:56').getAttribute('datetime')).toBe('2026-10-26T12:34:56.000Z');
    vi.advanceTimersByTime(60000);
    view.rerender(show(error));
    expect(screen.getByText('15:34:56')).toBeTruthy();
    view.rerender(show(new Error('GET /example → 502')));
    expect(screen.getByText('15:35:56')).toBeTruthy();
  });

  it('names what failed, shows the call and retries', () => {
    const retry = vi.fn();
    const error = new ApiError(
      'GET',
      '/tmf-api/accountManagement/v5/billingAccount',
      503,
      'Service Unavailable',
    );
    renderList(resource({ status: 'error', error, retry }));
    const alert = screen.getByRole('alert');
    expect(within(alert).getByText('Failed to load subscribers')).toBeTruthy();
    expect(
      within(alert).getByText(
        'GET /tmf-api/accountManagement/v5/billingAccount → 503 Service Unavailable',
      ),
    ).toBeTruthy();
    expect(alert.querySelector('svg[data-icon="octagon-alert"]')).toBeTruthy();
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it('shows a network failure without a status', () => {
    render(
      <ErrorBlock
        title="Failed"
        error={new ApiError('GET', '/internal/v1/context', 0, 'Network error')}
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText('GET /internal/v1/context → Network error')).toBeTruthy();
  });
});

describe('empty and no results', () => {
  it('explains an empty list and offers the action', () => {
    renderList(resource({ status: 'ok', data: { names: [] } }));
    expect(screen.getByText('No subscribers yet')).toBeTruthy();
    expect(
      screen.getByText('Subscribers appear after registration via CRM or import.'),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: '+ Add' })).toBeTruthy();
    expect(screen.queryByText('No matches')).toBeNull();
  });

  it('says "No matches" when filters hide every row, and resets them', () => {
    const { onReset } = renderList(
      resource({ status: 'ok', data: { names: ['Ivanov'] } }),
      'Petrov',
    );
    expect(screen.getByText('No matches')).toBeTruthy();
    expect(screen.queryByText('No subscribers yet')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(onReset).toHaveBeenCalledOnce();
  });

  it('names the query and every active filter', () => {
    const reset = vi.fn();
    render(
      <NoResults
        query="north"
        filters={[
          { label: 'Status', value: 'Suspended' },
          { label: 'Mode', value: 'Prepaid' },
        ]}
        onReset={reset}
      />,
    );
    expect(screen.getByText('Nothing matches “north”')).toBeTruthy();
    expect(screen.getByText('Suspended').parentElement?.textContent).toBe('with Status Suspended');
    expect(screen.getByText('Prepaid').parentElement?.textContent).toBe('with Mode Prepaid');
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it('renders the rows otherwise', () => {
    renderList(resource({ status: 'ok', data: { names: ['Ivanov', 'Petrov'] } }), 'ov');
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Ivanov',
      'Petrov',
    ]);
  });
});
