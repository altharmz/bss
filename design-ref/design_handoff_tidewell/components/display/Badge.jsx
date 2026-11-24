import React from "react";
const tones = {
  neutral: ["var(--surface-soft)", "var(--text-body)", "var(--sand-500)"],
  accent: ["var(--accent-soft)", "var(--tide-700)", "var(--accent)"],
  success: ["var(--success-soft)", "var(--success)", "var(--success)"],
  warning: ["var(--warning-soft)", "var(--warning)", "var(--warning)"],
  danger: ["var(--danger-soft)", "var(--danger)", "var(--danger)"],
  info: ["var(--info-soft)", "var(--info)", "var(--info)"],
  inverse: ["var(--surface-inverse)", "var(--text-inverse)", "var(--tide-200)"],
};
export function Badge({ tone = "neutral", dot = false, children, style }) {
  const [bg, fg, dc] = tones[tone] || tones.neutral;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 22, padding: "0 9px", borderRadius: "var(--radius-pill)", background: bg, color: fg,
      fontFamily: "var(--font-sans)", fontSize: "var(--caption-size)", fontWeight: 500, lineHeight: 1, whiteSpace: "nowrap", ...style }}>
      {dot && <span style={{ width: 6, height: 6, borderRadius: 9999, background: dc }} />}{children}
    </span>
  );
}
