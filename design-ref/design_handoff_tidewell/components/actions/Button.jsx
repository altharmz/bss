import React from "react";
const sizes = { sm: { h: "var(--control-sm)", px: 12, fs: 13, r: "var(--radius-sm)", g: 6 }, md: { h: "var(--control-md)", px: 14, fs: 14, r: "var(--radius-md)", g: 8 }, lg: { h: "var(--control-lg)", px: 18, fs: 15, r: "var(--radius-md)", g: 8 } };
const variants = {
  primary: { bg: "var(--accent)", hov: "var(--accent-hover)", fg: "var(--on-accent)", bd: "transparent" },
  secondary: { bg: "var(--surface-inverse)", hov: "var(--sand-700)", fg: "var(--text-inverse)", bd: "transparent" },
  outline: { bg: "var(--surface-card)", hov: "var(--surface-soft)", fg: "var(--text-strong)", bd: "var(--border-strong)" },
  ghost: { bg: "transparent", hov: "var(--surface-soft)", fg: "var(--text-strong)", bd: "transparent" },
  danger: { bg: "var(--danger)", hov: "#a33220", fg: "var(--sand-0)", bd: "transparent" },
};
export function Button({ variant = "primary", size = "md", disabled = false, fullWidth = false, iconLeft, iconRight, type = "button", onClick, children, style }) {
  const [h, setH] = React.useState(false), [p, setP] = React.useState(false), [f, setF] = React.useState(false);
  const s = sizes[size] || sizes.md, v = variants[variant] || variants.primary;
  return (
    <button type={type} disabled={disabled} onClick={onClick}
      onMouseEnter={() => setH(true)} onMouseLeave={() => { setH(false); setP(false); }} onMouseDown={() => setP(true)} onMouseUp={() => setP(false)}
      onFocus={e => setF(e.target.matches(":focus-visible"))} onBlur={() => setF(false)}
      style={{ display: fullWidth ? "flex" : "inline-flex", width: fullWidth ? "100%" : undefined, alignItems: "center", justifyContent: "center", gap: s.g,
        height: s.h, padding: `0 ${s.px}px`, borderRadius: s.r, border: `1px solid ${v.bd}`, background: h && !disabled ? v.hov : v.bg, color: v.fg,
        fontFamily: "var(--font-sans)", fontSize: s.fs, fontWeight: 600, letterSpacing: "-0.01em", whiteSpace: "nowrap", cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1, transform: p && !disabled ? "translateY(1px)" : "none", boxShadow: f ? "var(--ring-focus)" : "none", outline: "none",
        transition: "background var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out)", ...style }}>
      {iconLeft}{children}{iconRight}
    </button>
  );
}
