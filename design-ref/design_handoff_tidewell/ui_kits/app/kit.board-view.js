const { Button, IconButton, Icon, Avatar, Badge, Tag, Card, Input, Select, Checkbox, Radio, Switch, Tabs, Dialog, Toast, Tooltip } = window.TidewellDesignSystem_8a4ccf;
function BoardView({ tasks, onOpen, onAdd }) {
  const { columns } = window.TW_DATA;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(240px, 1fr))", gap: 16, alignItems: "start", minWidth: 1000 }}>
      {columns.map(col => {
        const list = tasks.filter(t => t.status === col.id);
        return (
          <section key={col.id} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <header style={{ display: "flex", alignItems: "center", gap: 8, height: 30, padding: "0 4px" }}>
              <Icon name={col.icon} size={15} color={col.id === "done" ? "var(--success)" : col.id === "progress" ? "var(--accent)" : "var(--text-muted)"} />
              <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-strong)" }}>{col.label}</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-subtle)" }}>{list.length}</span>
              <span style={{ flex: 1 }} />
              <IconButton icon="plus" label={"Add to " + col.label} size="sm" onClick={() => onAdd(col.id)} />
            </header>
            {list.map(t => <TaskCard key={t.id} task={t} onOpen={onOpen} />)}
            {list.length === 0 && <Card variant="outline" padding={18} style={{ textAlign: "center", fontSize: 13 }}>Nothing here yet</Card>}
          </section>
        );
      })}
    </div>
  );
}
Object.assign(window, { BoardView });
