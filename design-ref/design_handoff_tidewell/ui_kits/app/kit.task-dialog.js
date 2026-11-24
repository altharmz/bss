const { Button, IconButton, Icon, Avatar, Badge, Tag, Card, Input, Select, Checkbox, Radio, Switch, Tabs, Dialog, Toast, Tooltip } = window.TidewellDesignSystem_8a4ccf;
function TaskDialog({ task, isNew, onClose, onSave }) {
  const { people, projects, columns } = window.TW_DATA;
  const [draft, setDraft] = React.useState(task);
  React.useEffect(() => setDraft(task), [task]);
  if (!draft) return null;
  const set = (k, v) => setDraft(d => ({ ...d, [k]: v }));
  return (
    <Dialog open onClose={onClose} width={560} title={isNew ? "New task" : draft.title} description={isNew ? "Adds to This week" : draft.id + " · Updated 12 minutes ago"}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button onClick={() => onSave(draft)}>{isNew ? "Create task" : "Save changes"}</Button></>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {isNew && <Input label="Title" placeholder="Write launch email" value={draft.title} onChange={e => set("title", e.target.value)} />}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          <Select label="Status" value={draft.status} onChange={e => set("status", e.target.value)} options={columns.map(c => ({ value: c.id, label: c.label }))} />
          <Select label="Owner" value={draft.owner} onChange={e => set("owner", e.target.value)} options={people} />
          <Select label="Project" value={draft.project} onChange={e => set("project", e.target.value)} options={projects.map(p => ({ value: p.id, label: p.name }))} />
        </div>
        {draft.subtasks.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-strong)" }}>Checklist</span>
            {draft.subtasks.map((s, i) => <Checkbox key={i} label={s[0]} strike checked={s[1]} onChange={v => set("subtasks", draft.subtasks.map((x, j) => j === i ? [x[0], v] : x))} />)}
          </div>
        )}
        <Card variant="soft" padding={14} style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 13, color: "var(--text-body)" }}>Email the owner when this changes</span>
          <Switch defaultChecked />
        </Card>
      </div>
    </Dialog>
  );
}
Object.assign(window, { TaskDialog });
