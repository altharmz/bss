import React from "react";
import { IconButton } from "../actions/IconButton.jsx";
export function Dialog({ open, onClose, title, description, footer, width = 520, inline = false, children }) {
  if (!open) return null;
  const panel = (
    <div role="dialog" aria-modal="true" onClick={e => e.stopPropagation()} style={{ width: "100%", maxWidth: width, maxHeight: "calc(100vh - 64px)", display: "flex", flexDirection: "column",
      background: "var(--surface-card)", borderRadius: "var(--radius-xl)", boxShadow: "var(--shadow-dialog)", border: "1px solid var(--border-hairline)", overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 16, padding: "22px 24px 0" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {title && <h2 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 400, fontSize: 26, lineHeight: 1.15, letterSpacing: "-0.3px", color: "var(--text-strong)" }}>{title}</h2>}
          {description && <p style={{ margin: "6px 0 0", fontSize: 14, color: "var(--text-muted)" }}>{description}</p>}
        </div>
        {onClose && <IconButton icon="x" label="Close" size="sm" onClick={onClose} />}
      </div>
      <div style={{ padding: "18px 24px 22px", overflow: "auto" }}>{children}</div>
      {footer && <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, padding: "14px 24px", borderTop: "1px solid var(--border-hairline)", background: "var(--surface-page)" }}>{footer}</div>}
    </div>
  );
  if (inline) return panel;
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 32,
      background: "var(--surface-overlay)", backdropFilter: "blur(var(--blur-overlay))" }}>{panel}</div>
  );
}
