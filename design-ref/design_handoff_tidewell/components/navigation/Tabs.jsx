import React from "react";
export function Tabs({ items = [], value, onChange, variant = "underline", style }) {
  const seg = variant === "segmented";
  return (
    <div role="tablist" style={{ display: "inline-flex", gap: seg ? 2 : 20, padding: seg ? 3 : 0, borderRadius: seg ? "var(--radius-md)" : 0,
      background: seg ? "var(--surface-sunken)" : "transparent", borderBottom: seg ? "none" : "1px solid var(--border-hairline)", ...style }}>
      {items.map(it => {
        const on = it.id === value;
        return (
          <button key={it.id} role="tab" aria-selected={on} onClick={() => onChange && onChange(it.id)}
            style={{ all: "unset", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "var(--font-sans)", fontSize: 14, fontWeight: on ? 600 : 500,
              color: on ? "var(--text-strong)" : "var(--text-muted)", ...(seg
                ? { height: 28, padding: "0 12px", borderRadius: 7, background: on ? "var(--surface-card)" : "transparent", boxShadow: on ? "0 1px 2px rgba(27,30,28,.08)" : "none" }
                : { height: 36, marginBottom: -1, borderBottom: `2px solid ${on ? "var(--accent)" : "transparent"}` }) }}>
            {it.label}
            {it.count != null && <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-subtle)" }}>{it.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
