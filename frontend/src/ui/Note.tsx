import type { ReactNode } from 'react';
import { Icon } from './Icon';

/** Inline verdicts: info, warning and negative, each with its Lucide glyph. */
export function Note({
  tone,
  lead,
  code,
  children,
  className,
  'data-ui': ui,
}: {
  tone: 'info' | 'warning' | 'negative';
  lead?: string;
  code?: string | undefined;
  children: ReactNode;
  className?: string;
  'data-ui'?: string;
}) {
  return (
    <div
      className={`bss-note bss-note-${tone}${className ? ` ${className}` : ''}`}
      role={tone === 'negative' ? 'alert' : 'note'}
      data-ui={ui}
    >
      <Icon
        name={tone === 'info' ? 'info' : tone === 'warning' ? 'triangle-alert' : 'octagon-alert'}
        size={14}
      />
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
