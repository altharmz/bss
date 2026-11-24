# Tidewell web app — UI kit

Click-through mock of Tidewell's core planning surface. Composes the design-system components from `window.TidewellDesignSystem_8a4ccf`.

Screens / states
- **This week board** (default) — four status columns of TaskCards.
- **List view** — Tabs → List; table of the same tasks.
- **Project filter** — click a project in the sidebar.
- **Task dialog** — click any card; edit status/owner/checklist, save → toast.
- **New task** — "New task" button or column "+".
- **Inbox** empty state.
- **Sign in** — sign-out icon at the bottom of the sidebar.

Files: `kit.app.js` (state + layout), `kit.sidebar.js`, `kit.task-card.js`, `kit.board-view.js`, `kit.list-view.js`, `kit.task-dialog.js`, `kit.sign-in.js` (all JSX, loaded via type="text/babel"; lowercase .js names keep them out of the component bundle), `data.js`.

Note: Tidewell is an original brand created for this system; there is no production product to match against.
