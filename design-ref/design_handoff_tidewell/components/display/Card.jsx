import React from "react";
const variants = {
  default: { background: "var(--surface-card)", border: "1px solid var(--border-hairline)", color: "var(--text-body)" },
  soft: { background: "var(--surface-soft)", border: "1px solid transparent", color: "var(--text-body)" },
  dark: { background: "var(--surface-inverse)", border: "1px solid transparent", color: "var(--text-inverse)" },
  outline: { background: "transparent", border: "1px dashed var(--border-strong)", color: "var(--text-muted)" },
};
export function Card({ variant = "default", padding = 20, interactive = false, onClick, children, style }) {
  const [h, setH] = React.useState(false);
  return (
    <div onClick={onClick} onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{ borderRadius: "var(--radius-lg)", padding, ...variants[variant], cursor: interactive ? "pointer" : undefined,
        borderColor: interactive && h && variant === "default" ? "var(--border-strong)" : undefined,
        boxShadow: interactive && h ? "var(--shadow-pop)" : "var(--shadow-none)",
        transition: "box-shadow var(--dur-base) var(--ease-out), border-color var(--dur-base) var(--ease-out)", ...style }}>
      {children}
    </div>
  );
}
