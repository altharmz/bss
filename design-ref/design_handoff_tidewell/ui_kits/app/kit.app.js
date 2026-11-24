const { Button, IconButton, Icon, Avatar, Badge, Tag, Card, Input, Select, Checkbox, Radio, Switch, Tabs, Dialog, Toast, Tooltip } = window.TidewellDesignSystem_8a4ccf;
function App() {
  const D = window.TW_DATA;
  const [signedIn, setSignedIn] = React.useState(true);
  const [view, setView] = React.useState("week");
  const [project, setProject] = React.useState(null);
  const [tab, setTab] = React.useState("board");
  const [tasks, setTasks] = React.useState(D.tasks);
  const [open, setOpen] = React.useState(null);
  const [toast, setToast] = React.useState(null);
  React.useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 4000); return () => clearTimeout(t); }, [toast]);
  if (!signedIn) return <SignIn onSignIn={() => setSignedIn(true)} />;
  const proj = D.projects.find(p => p.id === project);
  const shown = project ? tasks.filter(t => t.project === project) : tasks;
  const doneN = shown.filter(t => t.status === "done").length;
  const save = d => {
    const exists = tasks.some(t => t.id === d.id);
    setTasks(exists ? tasks.map(t => t.id === d.id ? d : t) : [...tasks, d]);
    setToast(exists ? { title: "Changes saved", description: d.title } : { title: "Task created", description: d.title || "Untitled task" });
    setOpen(null);
  };
  const add = (status = "planned") => setOpen({ isNew: true, task: { id: "TW-" + (1060 + tasks.length), title: "", project: project || "launch", status, owner: "Maya Okafor", due: "Fri", subtasks: [], comments: 0 } });
  return (
    <div style={{ height: "100vh", display: "flex", background: "var(--surface-page)" }}>
      <Sidebar view={view} setView={setView} project={project} setProject={setProject} onSignOut={() => setSignedIn(false)} />
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <header style={{ padding: "28px 32px 0", display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 260 }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-subtle)", marginBottom: 8 }}>{proj ? "Project" : "Week 48"}</div>
              <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 400, fontSize: 44, lineHeight: 1.05, letterSpacing: "-0.88px", color: "var(--text-strong)" }}>
                {view === "inbox" ? "Inbox" : proj ? proj.name : <>This week <i style={{ color: "var(--text-muted)" }}>Nov 23 – 29</i></>}
              </h1>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ display: "flex", marginRight: 6 }}>{D.people.map((p, i) => <Avatar key={p} name={p} ring size={26} style={{ marginLeft: i ? -6 : 0 }} />)}</div>
              <Button variant="outline" iconLeft={<Icon name="share-2" />}>Share</Button>
              <Button iconLeft={<Icon name="plus" />} onClick={() => add()}>New task</Button>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Tabs items={[{ id: "board", label: "Board" }, { id: "list", label: "List", count: shown.length }]} value={tab} onChange={setTab} style={{ flex: 1 }} />
          </div>
        </header>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 32px" }}>
          <Input icon="search" placeholder="Filter tasks" size="sm" style={{ width: 240 }} />
          <IconButton icon="sliders-horizontal" label="Display options" variant="outline" size="sm" />
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-muted)" }}>{doneN} / {shown.length} done</span>
          <Badge tone="success" dot>On track</Badge>
        </div>
        <div style={{ flex: 1, overflow: "auto", padding: "4px 32px 32px" }}>
          {view === "inbox"
            ? <Card variant="outline" padding={48} style={{ textAlign: "center", maxWidth: 520, margin: "40px auto" }}>
                <Icon name="inbox" size={22} color="var(--text-subtle)" />
                <div style={{ fontFamily: "var(--font-display)", fontSize: 26, color: "var(--text-strong)", margin: "10px 0 4px" }}>You’re all caught up</div>
                <div style={{ fontSize: 14 }}>Mentions and assignments land here.</div>
              </Card>
            : tab === "board" ? <BoardView tasks={shown} onOpen={t => setOpen({ task: t })} onAdd={add} /> : <ListView tasks={shown} onOpen={t => setOpen({ task: t })} />}
        </div>
      </main>
      {open && <TaskDialog task={open.task} isNew={open.isNew} onClose={() => setOpen(null)} onSave={save} />}
      {toast && <div style={{ position: "fixed", left: 24, bottom: 24, zIndex: 70 }}><Toast tone="success" title={toast.title} description={toast.description} onClose={() => setToast(null)} /></div>}
    </div>
  );
}
Object.assign(window, { TidewellApp: App });
