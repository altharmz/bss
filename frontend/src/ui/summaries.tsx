import { Fragment, type ReactNode } from 'react';

export function PageHeader({
  title,
  count,
  meta,
  actions,
  filters,
}: {
  title: string;
  count?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  filters?: ReactNode;
}) {
  return (
    <header className="bss-page-header">
      <div className="bss-page-heading">
        <h1>{title}</h1>
        {count !== undefined && <span className="count">{count}</span>}
        {actions && <div className="actions">{actions}</div>}
      </div>
      {meta && <div className="bss-page-meta">{meta}</div>}
      {filters && <div className="bss-page-filters">{filters}</div>}
    </header>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="bss-key-value">
      {items.map(([label, value], index) => (
        <Fragment key={index}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

export interface MoneyFigure {
  ui?: string;
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'negative' | 'muted';
  emphasis?: 'figure' | 'answer';
}

/** Operators follow the figure at the same index; dividers precede their indexed figure. */
export function MoneyStrip({
  figures,
  operators = [],
  divider = [],
  actions,
}: {
  figures: MoneyFigure[];
  operators?: ('−' | '=' | undefined)[];
  divider?: number[];
  actions?: ReactNode;
}) {
  return (
    <div className="bss-money-strip">
      {figures.map((figure, index) => (
        <Fragment key={index}>
          {divider.includes(index) && <span className="money-divider" aria-hidden="true" />}
          <div
            data-ui={figure.ui}
            className={`money-figure${figure.emphasis ? ` emphasis-${figure.emphasis}` : ''}${figure.tone ? ` tone-${figure.tone}` : ''}`}
          >
            <span className="money-label">{figure.label}</span>
            <div className="money-value">{figure.value}</div>
            {figure.sub !== undefined && <div className="money-sub">{figure.sub}</div>}
          </div>
          {index < figures.length - 1 && operators[index] && (
            <span className="money-operator" aria-hidden="true">
              {operators[index]}
            </span>
          )}
        </Fragment>
      ))}
      {actions && <div className="money-actions">{actions}</div>}
    </div>
  );
}
