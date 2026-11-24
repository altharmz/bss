import { Icon } from './Icon';
import {
  useEffect,
  useLayoutEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type CSSProperties,
} from 'react';
import { createPortal } from 'react-dom';
import { Button } from './kit';

export interface TabOption<K extends string> {
  key: K;
  label: string;
  count?: number;
  badge?: ReactNode;
}

export function Tabs<K extends string>({
  id,
  label,
  options,
  value,
  onChange,
}: {
  id: string;
  label: string;
  options: TabOption<K>[];
  value: K;
  onChange: (key: K) => void;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div className="bss-tabs" role="tablist" aria-label={label}>
      {options.map((option, index) => (
        <button
          key={option.key}
          type="button"
          role="tab"
          id={`${id}-tab-${option.key}`}
          aria-controls={`${id}-panel-${option.key}`}
          aria-selected={value === option.key}
          tabIndex={value === option.key ? 0 : -1}
          ref={(element) => {
            buttons.current[index] = element;
          }}
          onClick={() => onChange(option.key)}
          onKeyDown={(event) => {
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? options.length - 1
                  : event.key === 'ArrowRight'
                    ? (index + 1) % options.length
                    : event.key === 'ArrowLeft'
                      ? (index - 1 + options.length) % options.length
                      : undefined;
            if (next !== undefined) {
              event.preventDefault();
              const option = options[next];
              if (option) onChange(option.key);
              buttons.current[next]?.focus();
            }
          }}
        >
          {option.label}
          {option.count !== undefined && (
            <>
              {' '}
              <span className="count">{option.count}</span>
            </>
          )}
          {option.badge && <> {option.badge}</>}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({
  id,
  value,
  children,
}: {
  id: string;
  value: string;
  children: ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={`${id}-panel-${value}`}
      aria-labelledby={`${id}-tab-${value}`}
      tabIndex={0}
    >
      {children}
    </div>
  );
}

export type MenuItem =
  | { label: string; onClick: () => void; disabled?: boolean; tone?: 'negative'; ui?: string }
  | { separator: true };

export function Menu({
  items,
  label = 'Row actions',
  trigger = <Icon name="ellipsis" />,
  ui,
  defaultOpen = false,
}: {
  items: MenuItem[];
  label?: string;
  trigger?: ReactNode;
  ui?: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const root = useRef<HTMLSpanElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const id = useId();
  const enabled = () =>
    Array.from(
      list.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [],
    );
  useLayoutEffect(() => {
    if (!open || !button.current || !list.current) return;
    const trigger = button.current.getBoundingClientRect();
    const menu = list.current.getBoundingClientRect();
    setPosition({
      top:
        window.innerHeight - trigger.bottom < menu.height
          ? Math.max(0, trigger.top - menu.height)
          : trigger.bottom,
      left: Math.max(0, Math.min(trigger.right - menu.width, window.innerWidth - menu.width)),
    });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (
        !root.current?.contains(event.target as Node) &&
        !list.current?.contains(event.target as Node)
      )
        setOpen(false);
    };
    const close = () => setOpen(false);
    document.addEventListener('click', outside, true);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      document.removeEventListener('click', outside, true);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [open]);
  const focusEdge = useRef<'first' | 'last' | null>(null);
  useEffect(() => {
    if (open && focusEdge.current) {
      const buttons = enabled();
      (focusEdge.current === 'last' ? buttons.at(-1) : buttons[0])?.focus();
      focusEdge.current = null;
    }
  }, [open]);
  return (
    <span
      className="bss-menu"
      ref={root}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
          button.current?.focus();
        }
      }}
    >
      <button
        type="button"
        className="bss-btn quiet small"
        ref={button}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        data-ui={ui}
        onClick={() => {
          focusEdge.current = 'first';
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            focusEdge.current = event.key === 'ArrowUp' ? 'last' : 'first';
            if (open) {
              const buttons = enabled();
              (event.key === 'ArrowUp' ? buttons.at(-1) : buttons[0])?.focus();
            } else setOpen(true);
          }
        }}
      >
        {trigger}
      </button>
      {open &&
        createPortal(
          <div
            className="bss-menu-items"
            ref={list}
            style={
              {
                '--menu-top': `${position.top}px`,
                '--menu-left': `${position.left}px`,
              } as CSSProperties
            }
            role="menu"
            aria-label={label}
            id={id}
            onKeyDown={(event) => {
              const buttons = enabled();
              const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
              const next =
                event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? buttons.length - 1
                    : event.key === 'ArrowDown'
                      ? (index + 1) % buttons.length
                      : event.key === 'ArrowUp'
                        ? (index - 1 + buttons.length) % buttons.length
                        : undefined;
              if (next !== undefined) {
                event.preventDefault();
                buttons[next]?.focus();
              }
            }}
          >
            {items.map((item, index) =>
              'separator' in item ? (
                <div role="separator" key={index} />
              ) : (
                <Button
                  key={index}
                  role="menuitem"
                  variant="quiet"
                  {...(item.tone ? { tone: item.tone } : {})}
                  disabled={item.disabled}
                  data-ui={item.ui}
                  onClick={() => {
                    setOpen(false);
                    button.current?.focus();
                    item.onClick();
                  }}
                >
                  {item.label}
                </Button>
              ),
            )}
          </div>,
          document.body,
        )}
    </span>
  );
}
