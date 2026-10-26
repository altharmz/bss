import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type CSSProperties,
  type SelectHTMLAttributes,
} from 'react';

// --- buttons ---

// The legacy variant remains only until slice 2b migrates its screen callers.
type Variant = 'primary' | 'secondary' | 'danger-outline' | 'danger' | 'quiet';

export function Button({
  variant = 'secondary',
  small = false,
  className = '',
  busy = false,
  busyLabel,
  tone,
  disabled,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  small?: boolean;
  busy?: boolean;
  busyLabel?: string;
  tone?: 'negative';
}) {
  return (
    <button
      type="button"
      className={`bss-btn ${variant}${small ? ' small' : ''}${busy ? ' is-busy' : ''}${tone ? ` tone-${tone}` : ''} ${className}`.trim()}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy && busyLabel ? busyLabel : children}
    </button>
  );
}

// --- page title and panel ---

export function PageTitle({ title, count }: { title: string; count?: ReactNode }) {
  return (
    <div className="bss-page-title">
      <h1>{title}</h1>
      {count !== undefined && <span className="count">{count}</span>}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  right,
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="bss-panel">
      {title !== undefined && (
        <header className="bss-panel-header">
          <span className="title">{title}</span>
          {subtitle !== undefined && <span className="subtitle">{subtitle}</span>}
          {right !== undefined && <span className="right">{right}</span>}
        </header>
      )}
      {children}
    </section>
  );
}

// --- status pill ---

export type Tone = 'active' | 'inactive' | 'design' | 'retired' | 'failed';

// Every status the UI shows, onto the design's five pill styles (D8: only colours that
// exist in the tokens). Statuses the design lacks take the closest meaning.
const TONES: Record<string, Tone> = {
  // design
  Active: 'active',
  completed: 'active',
  Inactive: 'inactive',
  'In Design': 'design',
  running: 'design',
  Retired: 'retired',
  failed: 'failed',
  // billing account state
  Suspended: 'inactive',
  Closed: 'retired',
  // product status (TMF637)
  active: 'active',
  created: 'design',
  pendingActive: 'design',
  pendingTerminate: 'inactive',
  suspended: 'inactive',
  terminated: 'retired',
  cancelled: 'retired',
  aborted: 'retired',
  // order state (TMF622); completed, failed and cancelled are above
  rejected: 'failed',
  inProgress: 'design',
  // a billing run's per-account outcome
  ok: 'active',
  blocked: 'inactive',
  // customer bill (TMF678): paid green, owed amber, held red, not issued yet blue
  settled: 'active',
  partiallyPaid: 'inactive',
  sent: 'inactive',
  onHold: 'failed',
  new: 'design',
  validated: 'design',
};

export function toneOf(status: string): Tone {
  return TONES[status] ?? 'inactive';
}

export function Pill({ status, label }: { status: string; label?: string }) {
  return <span className={`bss-pill ${toneOf(status)}`}>{label ?? status}</span>;
}

// --- inputs ---

export function TextInput({
  mono = false,
  width,
  className = '',
  style,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { mono?: boolean; width?: number }) {
  return (
    <input
      className={`bss-input${mono ? ' mono' : ''} ${className}`.trim()}
      style={
        width === undefined ? style : ({ ...style, '--input-width': `${width}px` } as CSSProperties)
      }
      {...rest}
    />
  );
}

export function Field({
  label,
  help,
  error,
  children,
}: {
  label: ReactNode;
  help?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="bss-field-control">
      <span className="field-label">{label}</span>
      {children}
      {help && <span className="field-help">{help}</span>}
      {error && <span className="field-error">{error}</span>}
    </label>
  );
}

export function SelectField({ className = '', ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className="bss-select-wrap">
      <select className={`bss-input bss-select ${className}`.trim()} {...rest} />
    </span>
  );
}

export function DateField(props: Omit<InputHTMLAttributes<HTMLInputElement>, 'width'>) {
  return (
    <span className="bss-date-wrap">
      <TextInput {...props} mono />
      <svg className="date-icon" aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
        <rect x="1.5" y="2.5" width="11" height="10" rx="1" />
        <path d="M1.5 5.5h11M4.5 1v3M9.5 1v3" />
      </svg>
    </span>
  );
}

export function MoneyField({
  currency = 'EUR',
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'width'> & { currency?: string }) {
  return (
    <span className="bss-money-field">
      <TextInput {...props} mono inputMode="decimal" />
      <span className="money-suffix">{currency}</span>
    </span>
  );
}

export function Checkbox({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} type="checkbox" className={`bss-checkbox ${className}`.trim()} />;
}

export interface SegmentOption<K extends string> {
  key: K;
  label: string;
  count?: number;
}

export function Segmented<K extends string>({
  options,
  value,
  onChange,
  label,
  as = 'filter',
}: {
  as?: 'filter' | 'radio';
  options: SegmentOption<K>[];
  value: K;
  onChange: (key: K) => void;
  label: string;
}) {
  return (
    <div className="bss-seg" role={as === 'radio' ? 'radiogroup' : 'group'} aria-label={label}>
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          role={as === 'radio' ? 'radio' : undefined}
          aria-checked={as === 'radio' ? option.key === value : undefined}
          aria-pressed={as === 'filter' ? option.key === value : undefined}
          onClick={() => onChange(option.key)}
        >
          {option.label}
          {option.count !== undefined && <span className="count">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

// --- modal ---

export function Modal({
  title,
  subtitle,
  width,
  onClose,
  footer,
  ui,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  width: number;
  onClose: () => void;
  footer?: ReactNode;
  /** The dialog's data-ui marker (docs/progress/scope.yaml). */
  ui?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="bss-overlay" onClick={onClose}>
      <div
        className="bss-modal"
        data-ui={ui}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{ '--modal-width': `${width}px` } as CSSProperties}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="bss-modal-header">
          <div>
            <h2>{title}</h2>
            {subtitle !== undefined && <div className="subtitle">{subtitle}</div>}
          </div>
          <button type="button" className="close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        {children}
        {footer !== undefined && <footer className="bss-modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

// --- toast: one at a time, bottom right, hides after 3.2 s ---

const TOAST_MS = 3200;
const ToastContext = createContext<(message: ReactNode) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<ReactNode>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const show = useCallback((text: ReactNode) => {
    clearTimeout(timer.current);
    setMessage(text);
    timer.current = setTimeout(() => setMessage(null), TOAST_MS);
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {message !== null && (
        <div className="bss-toast" role="status">
          {message}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): (message: ReactNode) => void {
  return useContext(ToastContext);
}
