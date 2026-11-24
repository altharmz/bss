import React from "react";
import { Icon } from "../display/Icon.jsx";
function Field({ label, hint, error, id, children }) {
  return (
    <label htmlFor={id} style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: "var(--font-sans)" }}>
      {label && <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-strong)" }}>{label}</span>}
      {children}
      {(error || hint) && <span style={{ fontSize: 12, color: error ? "var(--danger)" : "var(--text-muted)" }}>{error || hint}</span>}
    </label>
  );
}
export function Select({ label, hint, error, id, options = [], value, defaultValue, onChange, size = "md", disabled, style }) {
  const [f, setF] = React.useState(false);
  return (
    <Field label={label} hint={hint} error={error} id={id}>
      <span style={{ position: "relative", display: "flex", alignItems: "center", ...style }}>
        <select id={id} value={value} defaultValue={defaultValue} onChange={onChange} disabled={disabled} onFocus={() => setF(true)} onBlur={() => setF(false)}
          style={{ appearance: "none", width: "100%", height: size === "sm" ? 32 : 38, padding: "0 34px 0 12px", borderRadius: "var(--radius-md)", background: disabled ? "var(--surface-soft)" : "var(--surface-card)",
            border: `1px solid ${error ? "var(--danger)" : f ? "var(--accent)" : "var(--border-strong)"}`, boxShadow: f ? "var(--ring-focus)" : "none", outline: "none",
            fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--text-strong)", cursor: "pointer" }}>
          {options.map(o => typeof o === "string" ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <Icon name="chevrons-up-down" size={14} color="var(--text-subtle)" style={{ position: "absolute", right: 12, pointerEvents: "none" }} />
      </span>
    </Field>
  );
}
