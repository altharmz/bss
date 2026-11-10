import { useId, useState } from 'react';

export interface ComboboxOption {
  value: string;
  label: string;
}

export function Combobox({
  options,
  value,
  onChange,
  placeholder,
  disabled,
  ui,
  ariaLabel,
  onQueryChange,
  match = (label, query) => label.toLowerCase().includes(query.trim().toLowerCase()),
}: {
  options: ComboboxOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  ui?: string;
  ariaLabel?: string;
  /** The typed text, for options searched on the server (the list still filters locally). */
  onQueryChange?: (query: string) => void;
  /** Whether an option's label matches the typed text (default: contains, any case). */
  match?: (label: string, query: string) => boolean;
}) {
  const id = useId();
  const selected = options.find((option) => option.value === value);
  const [draft, setDraft] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const query = open && draft !== null ? draft : (selected?.label ?? '');
  const matches = options.filter((option) => match(option.label, query));

  const close = () => {
    setOpen(false);
    setDraft(null);
    onQueryChange?.('');
  };
  const choose = (option: ComboboxOption) => {
    onChange(option.value);
    close();
  };

  return (
    <div
      className="bss-combobox"
      data-ui={ui}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) close();
      }}
    >
      <input
        className="bss-input"
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={`${id}-listbox`}
        aria-activedescendant={
          open && matches[active] ? `${id}-option-${matches[active].value}` : undefined
        }
        value={query}
        placeholder={placeholder}
        disabled={disabled}
        onFocus={() => {
          setDraft(selected?.label ?? '');
          setActive(0);
          setOpen(true);
        }}
        onChange={(event) => {
          setDraft(event.target.value);
          onQueryChange?.(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
            if (matches.length > 0) {
              setActive((current) =>
                event.key === 'ArrowDown'
                  ? (current + 1) % matches.length
                  : (current - 1 + matches.length) % matches.length,
              );
            }
          } else if (event.key === 'Enter' && open && matches[active]) {
            event.preventDefault();
            event.stopPropagation();
            choose(matches[active]);
          } else if (event.key === 'Escape' && open) {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
        }}
      />
      {open && (
        <div className="bss-combobox-list" role="listbox" id={`${id}-listbox`}>
          {matches.length === 0 ? (
            <div className="bss-combobox-empty">No matches</div>
          ) : (
            matches.map((option, index) => (
              <div
                key={option.value}
                id={`${id}-option-${option.value}`}
                className={`bss-combobox-option${index === active ? ' is-active' : ''}`}
                role="option"
                aria-selected={option.value === value}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(option)}
              >
                {option.label}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
