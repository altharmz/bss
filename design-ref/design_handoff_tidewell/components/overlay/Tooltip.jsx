import React from "react";
export function Tooltip({ label, side = "top", open, children }) {
  const [h, setH] = React.useState(false);
  const show = open ?? h;
  const pos = side === "bottom" ? { top: "calc(100% + 6px)" } : { bottom: "calc(100% + 6px)" };
  return (
    <span onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)} style={{ position: "relative", display: "inline-flex" }}>
      {children}
      <span role="tooltip" style={{ position: "absolute", left: "50%", ...pos, transform: `translateX(-50%) translateY(${show ? 0 : side === "bottom" ? -2 : 2}px)`, opacity: show ? 1 : 0, pointerEvents: "none",
        whiteSpace: "nowrap", padding: "5px 8px", borderRadius: "var(--radius-sm)", background: "var(--surface-inverse)", color: "var(--text-inverse)",
        fontFamily: "var(--font-sans)", fontSize: 12, fontWeight: 500, transition: "opacity var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out)", zIndex: 60 }}>{label}</span>
    </span>
  );
}
