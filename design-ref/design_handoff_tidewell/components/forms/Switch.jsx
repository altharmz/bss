import React from "react";
export function Switch({ checked, defaultChecked = false, onChange, label, disabled, style }) {
  const [inner, setInner] = React.useState(defaultChecked);
  const on = checked ?? inner;
  const toggle = () => { if (disabled) return; setInner(!on); onChange && onChange(!on); };
  return (
    <label onClick={e => { e.preventDefault(); toggle(); }} style={{ display: "inline-flex", alignItems: "center", gap: 10, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
      fontFamily: "var(--font-sans)", fontSize: 14, color: "var(--text-strong)", ...style }}>
      <span role="switch" aria-checked={on} style={{ width: 32, height: 18, borderRadius: 9999, padding: 2, flex: "none", background: on ? "var(--accent)" : "var(--sand-300)", transition: "background var(--dur-base) var(--ease-out)" }}>
        <span style={{ display: "block", width: 14, height: 14, borderRadius: 9999, background: "var(--sand-0)", boxShadow: "0 1px 2px rgba(27,30,28,.2)",
          transform: on ? "translateX(14px)" : "none", transition: "transform var(--dur-base) var(--ease-out)" }} />
      </span>
      {label}
    </label>
  );
}
