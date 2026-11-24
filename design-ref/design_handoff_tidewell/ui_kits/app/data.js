window.TW_DATA = {
  people: ["Maya Okafor", "Jun Park", "Lena Fischer", "Omar Haddad", "Priya Raman"],
  projects: [
    { id: "launch", name: "Q3 Launch", color: "var(--tide-500)", count: 14 },
    { id: "brand", name: "Brand refresh", color: "var(--ember-500)", count: 8 },
    { id: "research", name: "Customer research", color: "var(--plum-600)", count: 5 },
    { id: "ops", name: "Team ops", color: "var(--blue-600)", count: 3 },
  ],
  columns: [
    { id: "planned", label: "Planned", icon: "circle-dashed" },
    { id: "progress", label: "In progress", icon: "circle-dot" },
    { id: "review", label: "Review", icon: "eye" },
    { id: "done", label: "Done", icon: "check-circle-2" },
  ],
  tasks: [
    { id: "TW-1042", title: "Draft launch email sequence", project: "launch", status: "progress", owner: "Maya Okafor", due: "Fri", subtasks: [["Outline 3 emails", true], ["Write first draft", true], ["Legal review", false]], comments: 4, health: "success" },
    { id: "TW-1038", title: "Pricing page copy pass", project: "launch", status: "review", owner: "Jun Park", due: "Thu", subtasks: [["Tier names", true], ["FAQ", true]], comments: 7, health: "success" },
    { id: "TW-1051", title: "Interview five churned accounts", project: "research", status: "planned", owner: "Priya Raman", due: "Next wk", subtasks: [["Pick accounts", false], ["Schedule calls", false]], comments: 1 },
    { id: "TW-1047", title: "Wordmark spacing studies", project: "brand", status: "progress", owner: "Lena Fischer", due: "Wed", subtasks: [["Three options", true], ["Review with Omar", false]], comments: 2, health: "warning" },
    { id: "TW-1033", title: "Q3 planning retro notes", project: "ops", status: "done", owner: "Omar Haddad", due: "Mon", subtasks: [["Share doc", true]], comments: 0 },
    { id: "TW-1055", title: "Update onboarding checklist", project: "ops", status: "planned", owner: "Jun Park", due: "Fri", subtasks: [], comments: 0 },
    { id: "TW-1049", title: "Partner webinar run of show", project: "launch", status: "planned", owner: "Maya Okafor", due: "Thu", subtasks: [["Speaker list", true], ["Slides", false], ["Dry run", false]], comments: 3, health: "danger" },
    { id: "TW-1029", title: "Synthesize survey themes", project: "research", status: "done", owner: "Priya Raman", due: "Tue", subtasks: [["Tag responses", true], ["Write summary", true]], comments: 5 },
  ],
};
