import React from "react";
import { Icon } from "../display/Icon.jsx";
const variants = {
  ghost: ["transparent", "var(--surface-soft)", "var(--text-muted)", "transparent"],
  outline: ["var(--surface-card)", "var(--surface-soft)", "var(--text-strong)", "var(--border-strong)"],
  primary: ["var(--accent)", "var(--accent-hover)", "var(--on-accent)", "transparent"],
};
export function IconButton({ icon, label, variant = "ghost", size = "md", active = false, disabled = false, onClick, style }) {
  const [h, setH] = React.useState(false);
  const [bg, hov, fg, bd] = variants[variant] || variants.ghost;
  const d = size === "sm" ? 28 : size === "lg" ? 40 : 34;
  return (
    <button type="button" aria-label={label} title={label} disabled={disabled} onClick={onClick} onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{ width: d, height: d, display: "inline-flex", alignItems: "center", justifyContent: "center", borderRadius: size === "sm" ? "var(--radius-sm)" : "var(--radius-md)",
        border: `1px solid ${bd}`, background: active ? "var(--accent-soft)" : h && !disabled ? hov : bg, color: active ? "var(--accent)" : h && variant === "ghost" ? "var(--text-strong)" : fg,
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.45 : 1, padding: 0, transition: "background var(--dur-fast) var(--ease-out)", ...style }}>
      <Icon name={icon} size={size === "sm" ? 14 : 16} />
    </button>
  );
}
