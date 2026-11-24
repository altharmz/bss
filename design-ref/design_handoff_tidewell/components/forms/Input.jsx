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
export function Input({ label, hint, error, icon, id, size = "md", disabled, style, ...rest }) {
  const [f, setF] = React.useState(false);
  const h = size === "sm" ? 32 : 38;
  return (
    <Field label={label} hint={hint} error={error} id={id}>
      <span style={{ display: "flex", alignItems: "center", gap: 8, height: h, padding: "0 12px", borderRadius: "var(--radius-md)", background: disabled ? "var(--surface-soft)" : "var(--surface-card)",
        border: `1px solid ${error ? "var(--danger)" : f ? "var(--accent)" : "var(--border-strong)"}`, boxShadow: f ? (error ? "0 0 0 3px rgba(191,59,40,.18)" : "var(--ring-focus)") : "none",
        transition: "border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out)", ...style }}>
        {icon && <Icon name={icon} size={15} color="var(--text-subtle)" />}
        <input id={id} disabled={disabled} onFocus={() => setF(true)} onBlur={() => setF(false)} {...rest}
          style={{ all: "unset", flex: 1, minWidth: 0, fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--text-strong)" }} />
      </span>
    </Field>
  );
}
