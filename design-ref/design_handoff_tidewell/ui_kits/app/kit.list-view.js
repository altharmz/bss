const { Button, IconButton, Icon, Avatar, Badge, Tag, Card, Input, Select, Checkbox, Radio, Switch, Tabs, Dialog, Toast, Tooltip } = window.TidewellDesignSystem_8a4ccf;
function ListView({ tasks, onOpen }) {
  const { columns, projects } = window.TW_DATA;
  const th = { textAlign: "left", fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 500, color: "var(--text-subtle)", padding: "10px 14px", borderBottom: "1px solid var(--border-hairline)", background: "var(--surface-page)" };
  const td = { padding: "11px 14px", borderBottom: "1px solid var(--border-hairline)", fontSize: 14, verticalAlign: "middle" };
  return (
    <Card padding={0} style={{ overflow: "hidden" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead><tr><th style={th}>Task</th><th style={th}>Project</th><th style={th}>Status</th><th style={th}>Owner</th><th style={th}>Due</th></tr></thead>
        <tbody>
          {tasks.map(t => {
            const col = columns.find(c => c.id === t.status), p = projects.find(x => x.id === t.project);
            return (
              <tr key={t.id} onClick={() => onOpen(t)} style={{ cursor: "pointer" }} onMouseEnter={e => e.currentTarget.style.background = "var(--surface-soft)"} onMouseLeave={e => e.currentTarget.style.background = ""}>
                <td style={td}><span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-subtle)", marginRight: 10 }}>{t.id}</span><span style={{ fontWeight: 600, color: "var(--text-strong)" }}>{t.title}</span></td>
                <td style={td}><Tag color={p.color}>{p.name}</Tag></td>
                <td style={td}><span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--text-body)" }}><Icon name={col.icon} size={14} color="var(--text-muted)" />{col.label}</span></td>
                <td style={td}><span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><Avatar name={t.owner} size={22} />{t.owner.split(" ")[0]}</span></td>
                <td style={{ ...td, color: "var(--text-muted)" }}>{t.due}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
Object.assign(window, { ListView });
