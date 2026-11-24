import React from "react";
import { Icon } from "../display/Icon.jsx";
export function Checkbox({ checked, defaultChecked = false, onChange, label, disabled, strike = false, style }) {
  const [inner, setInner] = React.useState(defaultChecked);
  const on = checked ?? inner;
  const toggle = () => { if (disabled) return; setInner(!on); onChange && onChange(!on); };
  return (
    <label onClick={e => { e.preventDefault(); toggle(); }} style={{ display: "inline-flex", alignItems: "center", gap: 10, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
      fontFamily: "var(--font-sans)", fontSize: 14, color: on && strike ? "var(--text-subtle)" : "var(--text-strong)", textDecoration: on && strike ? "line-through" : "none", ...style }}>
      <span role="checkbox" aria-checked={on} style={{ width: 16, height: 16, flex: "none", borderRadius: "var(--radius-xs)", display: "inline-flex", alignItems: "center", justifyContent: "center",
        background: on ? "var(--accent)" : "var(--surface-card)", border: `1.5px solid ${on ? "var(--accent)" : "var(--border-strong)"}`, transition: "background var(--dur-fast) var(--ease-out)" }}>
        {on && <Icon name="check" size={12} color="var(--on-accent)" />}
      </span>
      {label}
    </label>
  );
}
