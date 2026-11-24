import React from "react";
const tints = [["var(--tide-100)","var(--tide-700)"],["var(--ember-100)","var(--ember-700)"],["var(--plum-100)","var(--plum-600)"],["var(--blue-100)","var(--blue-600)"],["var(--amber-100)","var(--amber-600)"],["var(--green-100)","var(--green-600)"]];
export function Avatar({ name = "", src, size = 28, ring = false, style }) {
  const initials = name.split(" ").filter(Boolean).slice(0, 2).map(s => s[0]).join("").toUpperCase();
  const [bg, fg] = tints[[...name].reduce((a, c) => a + c.charCodeAt(0), 0) % tints.length];
  return (
    <span title={name} style={{ width: size, height: size, borderRadius: "var(--radius-pill)", display: "inline-flex", alignItems: "center", justifyContent: "center",
      background: src ? `center / cover url(${src})` : bg, color: fg, fontFamily: "var(--font-sans)", fontWeight: 600, fontSize: Math.round(size * 0.4), letterSpacing: "0.02em",
      boxShadow: ring ? "0 0 0 2px var(--surface-card)" : "none", flex: "none", ...style }}>{src ? null : initials}</span>
  );
}
