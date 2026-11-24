import React from "react";
import { Icon } from "../display/Icon.jsx";
const icons = { neutral: ["info", "var(--tide-200)"], success: ["check-circle-2", "#7fd1a4"], warning: ["alert-triangle", "#f0c36d"], danger: ["alert-octagon", "#f19a8a"] };
export function Toast({ tone = "neutral", title, description, action, onClose, style }) {
  const [ic, col] = icons[tone] || icons.neutral;
  return (
    <div role="status" style={{ display: "flex", alignItems: "flex-start", gap: 12, width: 360, maxWidth: "100%", padding: "12px 14px", borderRadius: "var(--radius-lg)",
      background: "var(--surface-inverse)", color: "var(--text-inverse)", boxShadow: "var(--shadow-pop)", fontFamily: "var(--font-sans)", ...style }}>
      <Icon name={ic} size={16} color={col} style={{ marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
        {description && <div style={{ fontSize: 13, color: "var(--sand-400)", marginTop: 2 }}>{description}</div>}
      </div>
      {action && <button onClick={action.onClick} style={{ all: "unset", cursor: "pointer", fontSize: 13, fontWeight: 600, color: "var(--tide-200)", marginTop: 1 }}>{action.label}</button>}
      {onClose && <button onClick={onClose} aria-label="Dismiss" style={{ all: "unset", cursor: "pointer", color: "var(--sand-500)", display: "inline-flex", marginTop: 2 }}><Icon name="x" size={14} /></button>}
    </div>
  );
}
