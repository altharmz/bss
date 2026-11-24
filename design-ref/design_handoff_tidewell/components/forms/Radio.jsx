import React from "react";
export function Radio({ checked = false, onChange, label, value, disabled, style }) {
  return (
    <label onClick={e => { e.preventDefault(); if (!disabled && onChange) onChange(value); }} style={{ display: "inline-flex", alignItems: "center", gap: 10, cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1, fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--text-strong)", ...style }}>
      <span role="radio" aria-checked={checked} style={{ width: 16, height: 16, flex: "none", borderRadius: 9999, background: "var(--surface-card)",
        border: `1.5px solid ${checked ? "var(--accent)" : "var(--border-strong)"}`, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
        {checked && <span style={{ width: 8, height: 8, borderRadius: 9999, background: "var(--accent)" }} />}
      </span>
      {label}
    </label>
  );
}
