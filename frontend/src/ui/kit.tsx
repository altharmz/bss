import { createPortal } from 'react-dom';
import { statusPresentation, type StatusKind } from './status';
import { useInspectorFit } from './inspectorFit';
import { Icon, type IconName } from './Icon';
export { Menu, Tabs, TabPanel } from './navigation';
export { Note } from './Note';
export { Icon, ICON_NAMES } from './Icon';
export type { IconName } from './Icon';
export { KeyValue, MoneyStrip, PageHeader } from './summaries';
export type { MenuItem, TabOption } from './navigation';
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

type Variant = 'primary' | 'secondary' | 'danger' | 'quiet';

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

/** An icon-only control (Tidewell IconButton): the label is its accessible name and
 * tooltip. */
export function IconButton({
  icon,
  label,
  small = false,
  className = '',
  ...rest
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'aria-label'> & {
  icon: IconName;
  label: string;
  small?: boolean;
}) {
  return (
    <button
      type="button"
      className={`bss-icon-btn${small ? ' small' : ''} ${className}`.trim()}
      aria-label={label}
      title={label}
      {...rest}
    >
      <Icon name={icon} size={small ? 14 : 16} />
    </button>
  );
}

/** A person's initials on a pill (Tidewell Avatar); the tint follows the name, so one
 * person always gets the same colour. */
export function Avatar({ name, initials }: { name: string; initials: string }) {
  const tint = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 6;
  return (
    <span className={`bss-avatar avatar-tint-${tint}`} aria-hidden="true">
      {initials}
    </span>
  );
}

// --- panel ---

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
          <h2 className="title">{title}</h2>
          {subtitle !== undefined && <span className="subtitle">{subtitle}</span>}
          {right !== undefined && <span className="right">{right}</span>}
        </header>
      )}
      {children}
    </section>
  );
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
      <Icon name="chevrons-up-down" size={14} className="select-icon" />
    </span>
  );
}

/** A file chooser: a button plus the chosen file's name (the native input stays hidden). */
export function FileField({
  label,
  fileName,
  onFile,
  accept,
}: {
  label: string;
  fileName?: string | null;
  onFile: (file: File | undefined) => void;
  accept?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <span className="bss-file-field">
      <input
        ref={input}
        type="file"
        className="bss-file-input"
        aria-label={label}
        {...(accept === undefined ? {} : { accept })}
        onChange={(event) => onFile(event.target.files?.[0])}
      />
      <Button small onClick={() => input.current?.click()}>
        Choose file
      </Button>
      <span className="bss-file-name">{fileName ?? 'No file chosen'}</span>
    </span>
  );
}

export function DateField(props: Omit<InputHTMLAttributes<HTMLInputElement>, 'width'>) {
  return (
    <span className="bss-date-wrap">
      <TextInput {...props} mono />
      <Icon name="calendar" size={14} className="date-icon" />
    </span>
  );
}

export function MoneyField({
  currency,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'width'> & { currency: string }) {
  return (
    <span className="bss-money-field">
      <TextInput {...props} mono inputMode="decimal" />
      <span className="money-suffix">{currency}</span>
    </span>
  );
}

/** A checkbox; with `label` it renders as <label class="bss-check"><input/> text</label>.
 * The native input stays (behaviour, labels, tests); the tick is an Icon drawn over it. */
export function Checkbox({
  className = '',
  label,
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { label?: ReactNode }) {
  const input = (
    <span className="bss-checkbox-box">
      <input {...props} type="checkbox" className={`bss-checkbox ${className}`.trim()} />
      <Icon name="check" size={12} className="checkbox-tick" />
    </span>
  );
  return label === undefined ? (
    input
  ) : (
    <label className="bss-check">
      {input}
      <span>{label}</span>
    </label>
  );
}

/** A native radio button with a text label. */
export function Radio({
  label,
  className = '',
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { label: ReactNode }) {
  return (
    <label className="bss-check">
      <input {...props} type="radio" className={`bss-radio ${className}`.trim()} />
      <span>{label}</span>
    </label>
  );
}

/** An on/off control: a native checkbox with the switch role. */
export function Switch({
  label,
  className = '',
  ...props
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'role'> & { label: ReactNode }) {
  return (
    <label className="bss-check">
      <input
        {...props}
        type="checkbox"
        role="switch"
        className={`bss-switch ${className}`.trim()}
      />
      <span>{label}</span>
    </label>
  );
}

export interface SegmentOption<K extends string> {
  key: K;
  label: string;
  /** The raw API code, kept for the tooltip when the label is a human one. */
  title?: string | undefined;
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
          title={option.title}
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

const DialogFooterContext = createContext<HTMLElement | null>(null);

/** Keeps a loaded child form's actions outside the scrolling body. */
export function DialogFooter({ children }: { children: ReactNode }) {
  const host = useContext(DialogFooterContext);
  return host ? createPortal(children, host) : null;
}

/** Share the exact click path, validation and write state of the visible primary. */
export function submitDialogPrimary(primary: HTMLButtonElement) {
  primary.click();
}

export function Dialog({
  title,
  headerActions,
  context,
  step,
  size,
  onClose,
  footer,
  ui,
  children,
  preview = false,
  inspector = false,
  onSubmit,
}: {
  title: string;
  headerActions?: ReactNode;
  onSubmit?: ((primary: HTMLButtonElement) => void) | undefined;
  context?: ReactNode;
  step?: { n: number; of: number };
  size: 'S' | 'M' | 'L';
  /** Static gallery rendering without a blocking overlay. */
  preview?: boolean;
  /** Render in place as a 400 px side panel (at >= 1536 px) instead of over the page. */
  inspector?: boolean;
  onClose: () => void;
  footer?: ReactNode;
  /** The dialog's data-ui marker (docs/progress/scope.yaml). */
  ui?: string;
  children: ReactNode;
}) {
  const [footerHost, setFooterHost] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (preview) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, preview]);

  const Shell = inspector ? 'aside' : 'div';
  const [fitRef, fitStyle] = useInspectorFit<HTMLElement>(inspector);
  return (
    <Shell
      ref={fitRef as never}
      style={inspector ? fitStyle : undefined}
      className={inspector ? 'bss-inspector' : preview ? 'bss-dialog-preview' : 'bss-overlay'}
      onClick={preview || inspector ? undefined : onClose}
    >
      <div
        className={`bss-modal bss-dialog-${size}`}
        data-ui={ui}
        role="dialog"
        aria-modal={preview || inspector ? undefined : true}
        aria-label={title}
        onKeyDown={(event) => {
          if (
            event.key !== 'Enter' ||
            event.nativeEvent.isComposing ||
            event.defaultPrevented ||
            !onSubmit
          )
            return;
          if (
            !(event.target instanceof HTMLInputElement) ||
            ![
              'text',
              'search',
              'email',
              'tel',
              'url',
              'password',
              'number',
              'date',
              'time',
              'datetime-local',
              'month',
              'week',
            ].includes(event.target.type)
          )
            return;
          const primary = event.currentTarget.querySelector<HTMLButtonElement>(
            ':scope > .bss-modal-footer .bss-btn.primary, :scope > .bss-modal-footer [data-dialog-primary]',
          );
          if (!primary || primary.disabled) return;
          event.preventDefault();
          onSubmit(primary);
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="bss-modal-header">
          <div>
            <div className="bss-dialog-title">
              <h2>{title}</h2>
              {step && (
                <span className="bss-dialog-step">
                  Step {step.n} of {step.of}
                </span>
              )}
            </div>
            {context !== undefined && <div className="subtitle">{context}</div>}
          </div>
          {headerActions}
          <IconButton icon="x" label="Close" small className="close" onClick={onClose} />
        </header>
        <DialogFooterContext.Provider value={footerHost}>
          <div className="bss-dialog-body">{children}</div>
        </DialogFooterContext.Provider>
        <footer ref={setFooterHost} className="bss-modal-footer">
          {footer}
        </footer>
      </div>
    </Shell>
  );
}

// --- toast: one at a time, bottom left, hides after 6 s ---

const TOAST_MS = 6000;
type ToastAction = { label: string; onClick: () => void };
type ShowToast = (message: ReactNode, action?: ToastAction) => void;
const ToastContext = createContext<ShowToast>(() => {});

export function ToastMessage({
  message,
  action,
  preview = false,
}: {
  message: ReactNode;
  action?: ToastAction | undefined;
  preview?: boolean;
}) {
  return (
    <div className={`bss-toast${preview ? ' bss-toast-preview' : ''}`} role="status">
      <Icon name="circle-check" className="toast-icon" />
      <span className="toast-title">{message}</span>
      {action && (
        <button type="button" onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<ReactNode>(null);
  const [action, setAction] = useState<ToastAction>();
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const show = useCallback<ShowToast>((text, nextAction) => {
    clearTimeout(timer.current);
    setMessage(text);
    setAction(nextAction);
    timer.current = setTimeout(() => setMessage(null), TOAST_MS);
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {message !== null && <ToastMessage message={message} action={action} />}
    </ToastContext.Provider>
  );
}

export function useToast(): ShowToast {
  return useContext(ToastContext);
}

/** D4 requires an explicit object kind; unknown states retain their raw label. */
export function StatusPill({
  kind,
  status,
  label,
}: {
  kind: StatusKind;
  status: string;
  label?: string;
}) {
  const [family, defaultLabel] = statusPresentation(kind, status);
  return (
    <span className={`status-pill pill-${family}`} title={status}>
      {label ?? defaultLabel}
    </span>
  );
}

/** A human label for an API code; the raw code stays in the tooltip. */
export function Coded({ code, label }: { code: string; label: string }) {
  return <span title={code}>{label}</span>;
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="bss-tag">{children}</span>;
}

export function Flag({ children }: { children: ReactNode }) {
  return <span className="bss-flag">{children}</span>;
}

export function Badge({
  kind = 'count',
  children,
}: {
  kind?: 'count' | 'failed' | 'info';
  children: ReactNode;
}) {
  return <span className={`bss-badge badge-${kind}`}>{children}</span>;
}
