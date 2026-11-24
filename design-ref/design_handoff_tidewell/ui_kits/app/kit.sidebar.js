const { Button, IconButton, Icon, Avatar, Badge, Tag, Card, Input, Select, Checkbox, Radio, Switch, Tabs, Dialog, Toast, Tooltip } = window.TidewellDesignSystem_8a4ccf;
function NavRow({ icon, label, count, active, onClick, swatch }) {
  const [h, setH] = React.useState(false);
  return (
    <button onClick={onClick} onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{ all: "unset", cursor: "pointer", display: "flex", alignItems: "center", gap: 10, height: 32, padding: "0 10px", borderRadius: "var(--radius-sm)",
        background: active ? "var(--surface-card)" : h ? "var(--surface-sunken)" : "transparent", boxShadow: active ? "0 0 0 1px var(--border-hairline)" : "none",
        color: active ? "var(--text-strong)" : "var(--text-body)", fontSize: 14, fontWeight: active ? 600 : 500 }}>
      {swatch ? <span style={{ width: 10, height: 10, borderRadius: 3, background: swatch, margin: "0 3px" }} /> : <Icon name={icon} size={16} color={active ? "var(--accent)" : "var(--text-muted)"} />}
      <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
      {count != null && <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-subtle)" }}>{count}</span>}
    </button>
  );
}
function Sidebar({ view, setView, project, setProject, onSignOut }) {
  const { projects } = window.TW_DATA;
  const sec = { fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-subtle)", padding: "18px 10px 6px" };
  return (
    <aside style={{ width: "var(--sidebar-width)", flex: "none", height: "100%", display: "flex", flexDirection: "column", padding: "14px 10px", background: "var(--surface-soft)", borderRight: "1px solid var(--border-hairline)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 10px 14px" }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 24, letterSpacing: "-0.4px", color: "var(--text-strong)", flex: 1 }}>Tidewell</span>
        <IconButton icon="search" label="Search ⌘K" size="sm" />
      </div>
      <NavRow icon="inbox" label="Inbox" count={3} active={view === "inbox"} onClick={() => { setView("inbox"); setProject(null); }} />
      <NavRow icon="calendar-days" label="This week" active={view === "week" && !project} onClick={() => { setView("week"); setProject(null); }} />
      <NavRow icon="users" label="Team" onClick={() => {}} />
      <div style={sec}>Projects</div>
      {projects.map(p => <NavRow key={p.id} swatch={p.color} label={p.name} count={p.count} active={project === p.id} onClick={() => { setProject(p.id); setView("week"); }} />)}
      <NavRow icon="plus" label="New project" onClick={() => {}} />
      <div style={{ flex: 1 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px", borderTop: "1px solid var(--border-hairline)" }}>
        <Avatar name="Maya Okafor" />
        <div style={{ flex: 1, minWidth: 0, lineHeight: 1.25 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-strong)" }}>Maya Okafor</div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Northwind Studio</div>
        </div>
        <Tooltip label="Sign out"><IconButton icon="log-out" label="Sign out" size="sm" onClick={onSignOut} /></Tooltip>
      </div>
    </aside>
  );
}
Object.assign(window, { Sidebar });
