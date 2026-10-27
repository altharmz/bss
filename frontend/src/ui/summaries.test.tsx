import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { KeyValue, MoneyStrip, PageHeader, PageTitle, Panel } from './kit';

describe('Ledger summaries', () => {
  it('keeps a money figure marker around its label, value and caption', () => {
    const { container } = render(
      <MoneyStrip
        figures={[
          { ui: 'test.figure', label: 'Available', value: '13.50', sub: 'ledger less holds' },
        ]}
      />,
    );
    const figure = container.querySelector('[data-ui="test.figure"]');
    expect(figure?.className).toBe('money-figure');
    expect(figure?.textContent).toBe('Available13.50ledger less holds');
  });
  it('pairs each key and value in a semantic definition grid', () => {
    const { container } = render(
      <KeyValue
        items={[
          ['Currency', 'EUR'],
          ['Cycle day', <strong>15</strong>],
        ]}
      />,
    );
    expect(container.querySelector('dl')?.className).toBe('bss-key-value');
    expect(
      Array.from(container.querySelectorAll('dt,dd')).map((node) => [
        node.tagName,
        node.textContent,
      ]),
    ).toEqual([
      ['DT', 'Currency'],
      ['DD', 'EUR'],
      ['DT', 'Cycle day'],
      ['DD', '15'],
    ]);
  });
  it('renders equation operators, dividers, emphasis, tone, captions and actions', () => {
    const { container } = render(
      <MoneyStrip
        figures={[
          { label: 'Ledger', value: '18.50', emphasis: 'figure' },
          { label: 'Reserved', value: '5.00', emphasis: 'figure' },
          {
            label: 'Available',
            value: '13.50',
            emphasis: 'answer',
            tone: 'negative',
            sub: 'After hold',
          },
        ]}
        operators={['−', '=']}
        divider={[2]}
        actions={<button>Top up</button>}
      />,
    );
    expect(
      Array.from(container.querySelectorAll('.money-operator')).map((node) => node.textContent),
    ).toEqual(['−', '=']);
    expect(container.querySelectorAll('.money-divider')).toHaveLength(1);
    expect(screen.getByText('13.50').parentElement?.className).toContain(
      'emphasis-answer tone-negative',
    );
    expect(screen.getByText('After hold')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Top up' }).parentElement?.className).toBe(
      'money-actions',
    );
  });
  it('keeps page heading slots and the compatibility title wrapper', () => {
    const { rerender, container } = render(
      <PageHeader
        title="Subscribers"
        count={0}
        meta="Accounts"
        actions={<button>New</button>}
        filters={<input aria-label="Search" />}
      />,
    );
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Subscribers');
    expect(container.querySelector('.count')?.textContent).toBe('0');
    expect(screen.getByText('Accounts').className).toBe('bss-page-meta');
    expect(screen.getByRole('textbox').parentElement?.className).toBe('bss-page-filters');
    rerender(<PageTitle title="Bills" count={3} />);
    expect(container.querySelector('.bss-page-header h1')?.textContent).toBe('Bills');
  });
  it('renders a panel header only when a title exists', () => {
    const { container, rerender } = render(
      <Panel title="In progress" subtitle="Order" right={<button>Open</button>}>
        Body
      </Panel>,
    );
    expect(screen.getByRole('heading', { name: 'In progress', level: 2 }).className).toBe('title');
    expect(container.querySelector('.subtitle')?.textContent).toBe('Order');
    rerender(<Panel>Body</Panel>);
    expect(container.querySelector('header')).toBeNull();
    expect(screen.getByText('Body').className).toBe('bss-panel');
  });
});
