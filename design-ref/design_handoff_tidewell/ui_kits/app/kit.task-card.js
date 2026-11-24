const { Button, IconButton, Icon, Avatar, Badge, Tag, Card, Input, Select, Checkbox, Radio, Switch, Tabs, Dialog, Toast, Tooltip } = window.TidewellDesignSystem_8a4ccf;
const healthLabel = { success: "On track", warning: "At risk", danger: "Blocked" };
function TaskCard({ task, onOpen }) {
  const proj = window.TW_DATA.projects.find(p => p.id === task.project);
  const done = task.subtasks.filter(s => s[1]).length;
  return (
    <Card interactive padding={14} onClick={() => onOpen(task)} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-subtle)" }}>{task.id}</span>
        {task.health && <Badge tone={task.health} dot>{healthLabel[task.health]}</Badge>}
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.35, color: task.status === "done" ? "var(--text-muted)" : "var(--text-strong)", letterSpacing: "-0.1px", textWrap: "pretty" }}>{task.title}</div>
      <div><Tag color={proj.color}>{proj.name}</Tag></div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 12, color: "var(--text-muted)" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Icon name="clock" size={13} />{task.due}</span>
        {task.subtasks.length > 0 && <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontFamily: "var(--font-mono)", fontSize: 11 }}><Icon name="list-checks" size={13} />{done}/{task.subtasks.length}</span>}
        {task.comments > 0 && <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Icon name="message-square" size={13} />{task.comments}</span>}
        <span style={{ flex: 1 }} />
        <Avatar name={task.owner} size={22} />
      </div>
    </Card>
  );
}
Object.assign(window, { TaskCard });
