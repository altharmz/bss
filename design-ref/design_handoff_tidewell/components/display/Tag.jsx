import React from "react";
import { Icon } from "./Icon.jsx";
export function Tag({ color, onRemove, children, style }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 24, padding: onRemove ? "0 4px 0 8px" : "0 8px", borderRadius: "var(--radius-sm)",
      background: "var(--surface-card)", border: "1px solid var(--border-hairline)", color: "var(--text-body)", fontFamily: "var(--font-sans)", fontSize: "var(--caption-size)", fontWeight: 500, whiteSpace: "nowrap", ...style }}>
      {color && <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />}
      {children}
      {onRemove && <button onClick={onRemove} aria-label="Remove" style={{ all: "unset", cursor: "pointer", display: "inline-flex", padding: 2, borderRadius: 4, color: "var(--text-subtle)" }}><Icon name="x" size={12} /></button>}
    </span>
  );
}
