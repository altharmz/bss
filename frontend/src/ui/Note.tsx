import type { ReactNode } from 'react';

/** Inline verdicts from the Ledger Components artboard. */
export function Note({
  tone,
  lead,
  code,
  children,
}: {
  tone: 'info' | 'warning' | 'negative';
  lead?: string;
  code?: string;
  children: ReactNode;
}) {
  return (
    <div className={`bss-note bss-note-${tone}`} role={tone === 'negative' ? 'alert' : 'note'}>
      <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
        {tone === 'info' ? (
          <>
            <circle cx="7" cy="7" r="6" />
            <path d="M7 6v4M7 4v.01" />
          </>
        ) : tone === 'warning' ? (
          <>
            <path d="M7 1.5l6 11H1z" />
            <path d="M7 6v3M7 10.8v.01" />
          </>
        ) : (
          <rect x="3" y="3" width="8" height="8" rx="1" transform="rotate(45 7 7)" />
        )}
      </svg>
      <div>
        {lead && (
          <>
            <strong>{lead}</strong>{' '}
          </>
        )}
        {children}
        {code && (
          <>
            {' '}
            <span className="bss-note-code">{code}</span>
          </>
        )}
      </div>
    </div>
  );
}
