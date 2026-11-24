# Slice patterns

The shapes a UI slice repeats, with minimal examples taken from the code. Read this
before opening `FinancialAccounts.tsx`, `Parties.tsx` or `Payments.tsx`; open those only
for what is not covered here. **When a slice changes one of these patterns, update this
file in the same commit.**

## 1. New screen (route, sidebar, markers)

A screen touches four files besides its own (example: `payments`):

- `frontend/src/app/router.tsx`: add `| { name: 'payments' }` to the route union, a path
  match `if (path === '/payments' || path === '/payments/') return { name: 'payments' };`
  and a `case 'payments': return '/payments';` in the href function.
- `frontend/src/app/App.tsx`: `case 'payments': return <PaymentsScreen />;`
- `frontend/src/app/Shell.tsx`: a sidebar item `{ label: 'Payments', href: '/payments',
  active: (r) => r.name === 'payments' }` in its section (no count request unless the
  design asks for one).
- `frontend/src/app/App.test.tsx`: the expected sidebar links gain the new href
  (an existing test: say `TEST-CHANGE:` in the commit message).

A screen file starts with the design reference comment, then imports from `../api/*`,
`../app/context` (`useSetCrumbs`, `useSystemContext`), `../app/router` (`Link`, `hrefOf`),
`../ui/kit` (`Dialog`, `PageHeader`, `Panel`, `StatusPill`, `Button`, ...), `../ui/states`
(`ErrorBlock`, `ListState`, `SkeletonRows`), `../ui/Table` and `./screens.css`.
Interactive and visible parts carry `data-ui="ui.<area>.<name>"`.

## 2. Reading data

`useResource(key, load)` (`api/useResource.ts`) is the only loader: states `loading |
error | ok` plus `retry()`. A new `key` reloads and aborts the previous request.

```ts
const payments = useResource(`payments:${filter.account}:${filter.status}`,
  (signal) => loadPayments(filter, signal));
```

An API module (`api/payments.ts`) holds the types, a query builder and thin loaders:

```ts
export function loadPayments(filter: HistoryFilter, signal?: AbortSignal) {
  return getAll<PaymentRecord>(query(PAYMENTS, filter), signal);   // list
}
export function loadPayment(id: string, signal?: AbortSignal) {
  return getJson<PaymentRecord>(`${PAYMENTS}/${id}`, signal);      // one
}
```

`getAll`/`getJson` come from `api/client`; resource base paths (`PAYMENTS`...) from
`api/bss.ts`. Every list screen renders `ErrorBlock` + Retry on error and an empty state.

## 3. Writing (dialogs)

`useWrite(onDone)` and `Footer` (`screens/FinancialForms.tsx`) give `busy`, `problem`
and `unsure` (a 5xx or network error: "Unsure whether it was saved", with Refresh):

```tsx
const write = useWrite(onDone);
...
write.run(async () => { await createThing(body); return 'Thing created'; });
<Footer write={write} onRefresh={onRefresh}><Button ...>Create</Button></Footer>
```

The string `run` returns is the toast. A refusal (409/422) shows the server's reason and
keeps what the operator typed. A PATCH sends only changed fields, `null` for a cleared
one. Only the order POSTs and the prepaid balance actions carry an `Idempotency-Key`,
one per submission, as the design says. Deletes send no body.

## 4. Component test

`stubFetch(routes)` (`test/fetch.ts`) maps `"METHOD path?query"` to a handler. An
unmatched request fails the test, so a new screen's reads need stubs, and so do the
existing tests that now trigger them (an edit of an existing stub: `TEST-CHANGE:`).
Standard routes: `CONTEXT`, `NAV_RUNS`/`noRuns`, the session route; see the
`backend()` helper at the top of `Payments.test.tsx`. Pin every write body as an exact
string and mark the visible elements:

```ts
fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
await screen.findByText('Treasury created');
expect(writes(fetchMock)).toEqual([
  ['POST', `${ACC}/partyAccount`,
   JSON.stringify({ '@type': 'PartyAccount', name: 'Treasury', relatedParty: [OWNER_P1] })],
]);
expectUi('ui.accounts.party.form');   // asserts data-ui and records it for the tracker
```

## 5. Replay test (backend)

`tests/internal/test_ui_requests.py` sends the same request strings to the real API with the
`api` fixture and `tests/apidata.py` factories; it asserts the status and what is
stored. One test per slice, named `test_<what>_as_the_ui_sends_them`:

```python
def test_party_account_writes_as_the_ui_sends_them(api):
    created = api.post_raw(f"{acc}/partyAccount",
        '{"@type":"PartyAccount","name":"Treasury","relatedParty":[' + owner_json + ']}')
    assert created.status_code == 201, created.text
```

Use `post_raw`/`patch_raw` when the exact JSON text matters (key order); reads: `api.get(url)`.
A read-only slice replays the query strings its component test asserts.

## 6. Docs every UI slice updates

- `docs/ui.md`: the screen's entry.
- `docs/progress/scope.yaml`: one line per marker, `{id: ui.<area>.<name>, area: ui,
  stage: 5, title: "..."}`.
- The decision entry the slice section names (what was built, tests, deviations).

## Ledger v2 styles and visual references

R11 money forms use M Dialog with name and copyable BA Id context, MoneyField inside
Field, loaded Balance after, and amount-aware primary labels. Transfer separates
Recipient, Amount (before/after KeyValue) and Confirm; only Confirm can write, after an
acknowledgement, and its final button cancels Enter. Record payment reuses Footer for
refused/unsure states. Prepaid money actions retain idempotent uncertain Retry contracts.

R10 Add product: an L Dialog places selectable offering rows, number/start fields and immediate-shortfall warning on the left, with a dedicated first-charge preview rail on the right. `Footer` accepts optional `left` content in `.bss-write-left` (here the server-preview timestamp); its action children still occupy the right. The Add button is secondary when a same-day shortfall makes footer Top up primary. Draft actions keep their existing markers and requests.

R9 catalog: use Tabs/TabPanel for sections, page tables grouped by lifecycle with filtered group counts, and an L offering Dialog with lifecycle stepper, price figure, KeyValue and compact price history. Dialog accepts optional `headerActions` for an existing Menu; action markers belong on its menuitems. Missing dates render “—” or omit transition captions rather than synthesizing history. Non-sellable rows carry a Not sellable Tag in every lifecycle group. New offering loads category choices and sends description, sellability and selected category references; Add price posts the charge, then patches the complete old-plus-new price-reference list. Usage is offered only on pay-as-you-go offerings, with an explicit unit and block amount; the client checks whole-byte/second/message blocks and overlapping kind/currency prices, while the server remains authoritative. Usage-price edits send only name, description and amount, never the fixed type or unit.

`ui/base.css` imports `tokens.css`, then the five font faces. CSS uses the Ledger role and
type tokens only (`legacy-tokens.css` is gone); `--size-*` tokens give a size where a rule
sets only `font-size`. `ui/styleGuard.test.ts` checks CSS literals, font weights, that every
`var(--…)` is defined in `tokens.css` or set at runtime by a `style={{ '--x': … }}` prop (d),
and that `style={{` appears only with such a custom property (e). `make web-shots` captures seeded pages at both design widths without billing
writes; screenshot output is ignored.

### Ledger controls (slice 2a)

`Button` supports primary, secondary, danger and quiet variants, `small`, quiet
`tone="negative"`, and `busy`/`busyLabel`. Busy disables the button and adds its spinner.
The legacy danger-outline variant remains for the screen migrations in 2b.
`Field` wraps its control in a label, with optional `help` and `error`. `SelectField`,
`DateField`, `MoneyField` (string value and required currency suffix) and `Checkbox` preserve native
handlers. `TextInput` retains its `mono` and runtime `width` API.
`Combobox` filters labelled options as the operator types, supports ArrowUp/ArrowDown, Enter and
Escape, and exposes combobox/listbox semantics. It has an optional `ariaLabel`. Users: financial-account owners (G10) and the subscriber-number and IMSI pickers of Add product and Subscriber number (G8). Offering lists keep their filtered radio lists, which carry price columns.
`Segmented` defaults to filter buttons (`aria-pressed`); `as="radio"` uses radiogroup/radio
semantics. `/ui-kit` previews these primitives without screen-specific reads or writes.

### Ledger status and attribute primitives (slice 2b1)

`StatusPill` requires `kind` (a D4 object kind) and `status`, and accepts a custom
`label`. `ui/status.ts` owns the mapping. Unknown values use the Draft family and their
raw text. Each family has a CSS glyph as well as its colour. `Tag` identifies fixed
attributes, `Flag` shows short warning facts, and `Badge` takes `count` (default),
`failed`, or `info`. Each takes children. `/ui-kit` renders all D4 values and variants.
Every screen caller now passes its D4 kind; legacy Pill and danger-outline compatibility
are removed. Destructive text actions use `variant="quiet" tone="negative"`.

`Amount({ cents, currency?, signed? })` uses mono tabular figures, U+2212 negatives and
neutral positive values. Use `signed` for movements; use an `amount-strong` wrapper for
body-strong emphasis. `Id({ id, prefix })` displays prefix + uppercase short UUID and
copies the full UUID, toasting success or failure without activating an enclosing row.
The Subscribers list keeps Subscriber no. and adds a BA Id column; the card meta also
uses Id. Other display ids migrate in the later screen slices.
`TableRow` accepts a `className` for screen state styling.

### Ledger table and list foundations (slice 3a)

`TableHead` and `TableRow` accept `density="default"|"compact"|"dense"`.
Clickable rows enter the tab order and activate on Enter/Space; nested control key events
stay with the control. `selected` adds the selection class and `aria-selected`.
Column `kind="actions"` reserves a hover/focus-visible action cell; Menu integration
and the structured footer range remain in slice 3b.
`SkeletonRows` retains real row geometry immediately and reveals bars after 200 ms via
`useDelayed`. `NoResults` and `ListState` accept optional `query` and
`filters: { label, value }[]`; query-aware copy names both. Omit query to retain the
existing accessible “No matches” label until a screen's design migration.

### Ledger navigation and table actions (slice 3b1)

`Tabs({ id, label, options, value, onChange })` uses roving focus and automatic selection
with Left/Right/Home/End. Each option has a key, label, optional count or badge.
`TabPanel({ id, value })` links its label to the corresponding tab; share the same id.
`Menu({ items, label?, trigger?, ui? })` supplies disabled and negative items and
`{ separator: true }`, keyboard navigation, Escape refocus and outside-click dismissal.
The default trigger is a 28 px ellipsis named Row actions. ProductActions keeps its
existing Actions accessible name. `defaultOpen` supports the gallery's open sample. Items render in a document-body portal
with fixed viewport coordinates, right-aligned below the trigger or flipped above when
space below is insufficient. Scroll and resize dismiss the menu. Keyboard navigation,
outside-click dismissal and focus return remain shared by all callers.
`TableRow` accepts `actions: MenuItem[]` for a `kind="actions"` cell; menu clicks do
not activate the row. `TableFooter` accepts `range: { from, to, total }`, with numeric
range emphasis; existing `left`/`right` slots remain for screen migration and paging.

### Ledger notes and failure timestamps (slice 3b2a)

`Note({ tone, lead?, code?, children })` supports info, warning and negative tones.
Negative notes have role alert; the other tones have role note. Leads are bold,
server codes are mono ink-3, and each tone has its own decorative SVG glyph.
The gallery previews all three tones. All legacy screen note callers migrated in 3b2b2b2; `className` and `data-ui` preserve caller layout and markers.
`ErrorBlock` captures each error's failure instant once, displaying HH:MM:SS in the
system context's business zone. Bootstrap errors preserve their call and Retry when
context is unavailable, omitting the timestamp until context exists.

### Ledger summary foundations (slice 3b2b1)

`PageHeader({ title, count?, meta?, actions?, filters? })` supplies the title/count row,
right actions, metadata and wrapping filter row. It is the only page title.
`KeyValue({ items })` renders pairs of React nodes as a semantic two-column definition list.
`MoneyStrip({ figures, operators?, divider?, actions? })` takes labelled value/caption
figures. `emphasis="figure"` enlarges inputs; `emphasis="answer"` enlarges and emphasizes
the answer label. Optional negative/muted tones affect the value. Operators follow the
figure at their index; divider indices insert rules before figures. The actions slot
sits beside the figures. `Panel` uses a 44 px header only when a title is provided.

### Ledger success toast (slice 3b2b2a)

`useToast()` accepts `(message, action?: { label, onClick })`. Each call replaces the
previous message and action and starts a fresh six-second timeout. Toasts appear at the
bottom-left. `ToastMessage` shares the visual rendering with the gallery's static
`preview`; the gallery also offers a live Show toast action.

### Ledger dialogs (slice 3b2b2b1)

`Dialog({ size: 'S'|'M'|'L', title, context?, step?: { n, of }, footer?, ui?, onClose })`
uses fixed 400/560/760 px sizes, a padded body and subtle footer. The dialog is a
viewport-bounded column (`100dvh` less two `--space-8` gutters); only
`.bss-dialog-body` scrolls, while header/footer stay fixed.
Loaded child forms use `DialogFooter` to portal actions into the fixed footer without
moving form state. Native form submit buttons keep an explicit `form` id association
when portaled, preserving browser validation. Form callers pass optional
`onSubmit={submitDialogPrimary}` to activate the enabled footer primary on Enter from
a single-line input. Legacy secondary-styled main actions may explicitly designate
their target with `data-dialog-primary`, preserving their current appearance. Textareas,
non-text controls, composition and destructive buttons are excluded; transfer confirms
pass `undefined` to opt out explicitly, as do destructive End and cancellation panels.
Order metadata's Save details lives in the footer and owns Enter while that panel is
open; draft Submit stays available as a secondary action. Its context line sits
under the title; the step follows the title. `preview` renders static gallery examples
without an overlay or Escape handler.
Existing callers' `bss-modal-body` containers own the same 20 px body spacing without
double padding. Escape, overlay clicks and the close button preserve closing behavior.

### Ledger write footer (slice 3b2b2b2)

`Footer` keeps Cancel/Close and write buttons as direct children. It disables buttons while busy, reserves space for the write action’s spinner and both labels to keep its width stable while busy, and replaces write actions with primary Refresh when unsure. Refusals use a negative Note with “Refused.”; uncertain outcomes use the D7 warning and `useWrite.failedCall`. Optional `write.code` is presentation data. `ApiError.code` holds the TMF Error's business code when the server sends one; `explain` turns the account security codes into operator sentences (issue 226). Editable forms using Footer wrap fields in a disabled fieldset while busy. UI kit shows idle, busy, refused and unsure dialogs; Numbers is included in reference captures.

### Ledger shell (slice 4)

`Shell` keeps existing navigation requests and session-menu semantics. Sidebar active
links use `is-active` as well as `aria-current`. `TopBar` compares `context.businessDate`
with `businessDate(context.now, context.timeZone)`; drift is calendar-day arithmetic,
independent of the browser clock and time zone. The chip has `ui.shell.businessDate`;
the operator summary has `ui.shell.operator`. Session has no display-name field, so
login and its first two uppercase characters appear in the footer.

### Ledger Subscribers list (slice 5)

Subscribers uses PageHeader's wrapping filters and the page-level table, with a 300 px
search and `/` shortcut outside editable fields. The count follows all client-side
filters. Name, BA Id, account status, mode, products, cycle and final currency-free balance
form its seven columns. The over-limit Flag precedes Amount only below the credit-limit
boundary; NoResults receives query and labelled Status/Mode filters.

### Ledger subscriber header (slice 6a)

The subscriber identity uses a borderless semantic header with a page title, account StatusPill and mode Tag. The metadata row uses the customer StatusPill and existing account links. Account actions use Menu with per-item ui markers; postpaid Record payment and conditional Delete… live in More actions. Slice 6b1 replaces the KPI panel with prepaid/postpaid MoneyStrip and a borderless Account KeyValue summary. MoneyFigure accepts optional `ui` for a marker on the complete labelled figure, preserving figure captions and values together. Prepaid relative days use only context.businessDate. All existing eager loaders and dialog ownership stay in place; tabs, loaded counts and the Overview grid follow in 6b2.

### Ledger subscriber tabs (slice 6b2)

Tabs separate the label from an optional count or badge with literal whitespace, preserving readable accessible names such as "Payments 0" and "Commands 1".

The card uses Tabs/TabPanel with selected keys in the URL hash. Empty, unknown or mode-inapplicable hashes fall back to Overview. Keep all panel components mounted behind native hidden wrappers so tab selection neither triggers reads nor cancels polling. PaymentsPanel, RefundsPanel and FailedCommands accept an optional onCount callback for their already loaded list size; undefined means loading or failed. Card counts never add requests. Overview has minmax(0, 1fr) plus a 320/400 px rail with a 32 px gap; other panes fill the width. Dialog ownership stays with the original components.

### Ledger recent activity (slice 7a)

Overview uses the first eight already loaded transactions in server order. The rail pairs note/type and signed amount with the business date and balanceAfter; dates are date-only API values, so no local time conversion is applied. All transactions selects the mounted Transactions pane without fetching. Empty and failed reads remain distinct; prepaid accounts explain ledger versus reserved funds.

### Order refusal copy (GUI review G2a)

`api/orderErrors.ts` maps TMF622 business codes to operator sentences and falls back to
unknown codes' server reason. `OrderOutcome` preserves the raw errors and exposes
`refusal: {problem, code}` for Footer. Number changes use the same mapper on their raw
order response. A rejected order never calls the success/close callback. The exported
JSON code list is checked against both the TypeScript mapping and backend problem tuples.
Edit draft saves through AddProductModal; its order detail owns Submit.

### Refresh-safe writes (GUI review G3)

`api/WriteLock` supplies a shared counter via `WriteLockProvider`/`useWriteLock`.
Transport writes acquire/release it, and `useWrite` holds it across the complete action
callback. Nested writes cannot unlock each other. `usePolled` pauses background reads
while locked; card reloads queue through `writeLock.afterWrites`. `useResource` accepts
an optional third `keepPrevious` argument for same-entity refreshes: keep the mounted
view and pause its reload while locked. Failed background reloads retain that view and
expose `refreshError` with Retry. Entity navigation still needs a keyed owner.
Card dialogs belong to card state, outside list rows, with stable product/action ids;
number and balance-action panels pass their selection up. Shared writes whose callback
resolves without a transport request show “Not sent. Try again.” instead of success.
Successful count-changing writes invalidate `NavVersionProvider`; all sidebar count
resources include that version in their key.


### Specification documents (GUI review G4b)

`Specifications.tsx` combines text fields, a subscriber-number requirement checkbox and
repeatable characteristic fieldsets inside the scrolling dialog body, using the shared
write Footer. A requirement-specific 409 restores and locks that checkbox while keeping
other edits. Arrays are replaced only when edited; unrelated resource references and
untouched characteristic documents are preserved. Characteristic rows validate typed values
before serializing TMF620 documents; the first comma-separated value is default.
Boolean metadata remains editable, but values are disabled because the API's vendored
polymorphic mapping rejects boolean value documents.

### Ledger subscriber products and progress (slice 7b)

Products use compact rows with separate status/since cells. ProductActionsMenu accepts
extra MenuItem entries so Subscriber number, Top up and Deactivate share its ellipsis
trigger; the Actions accessible name remains. Pending changes and deactivation Undo
render as full-width sibling sub-rows. AccountProduct has no number value, so Number
shows a dash and the existing number dialog remains lazy. Next charge remains a Since
caption, preserving its existing value.

Accepted watch state belongs to SubscriberCardScreen; its rendered progress is placed
inside the persistently mounted Card Overview pane. Never key the progress by refresh
version or conditionally unmount it on a tab switch. WatchProgress renders command phases
and usePolled.lastCheckedAt in the business time zone without changing timers or requests.
Terminal settlement still dismisses the watch and toasts the result immediately.

### Subscriber unfinished orders (GUI review G5)

SubscriberOrders reuses loadOrderPage for each of draft, acknowledged and inProgress, paging by account and state. Open reuses OrderModal, with refresh and watch ownership retained by the card. DraftPrice selects the attached recurring catalog price valid at the requested start’s business-zone midnight; reads and failures remain distinct. Immediate prepaid Add product and expired Resume disable submission while the server preview is unavailable or reports a shortfall, and reuse Preview’s Top up handoff.

### Ledger billing runs (R8)

The latest-run band and selected compact history share the existing loaded run list. Outcomes replaces the dialog but keeps its lazy detail/account loaders, firstFilter and exact filter queries: activate Accounts or select a history row. Key the outcomes owner by run id so selection resets its filter. Run details stays in a disclosure; account names link to cards beside copyable Id controls. The next-run countdown uses zonedDateTime for latest date + 1 at billingRunAt, including the target date's DST offset, and context.now only. Outcomes/history wrap below the band when their compact tables cannot fit side by side.

### Account PIN on sensitive writes (issue 226)

A dialog whose request the server may verify (`docs/decisions/accounts.md`) renders
`AccountPinField` (`screens/SecurityTab.tsx`, marker `ui.security.accountPin`) and passes the
value to its API function. That function adds `accountPinHeader(pin)`, which is `{}` when the
field is empty, to `postJson`/`patchJson`/`request`. The PIN never goes in a body, a URL or a
toast, and the field is cleared after a successful write. Refusal copy comes from
`ACCOUNT_SECURITY_ERRORS`, which `orderRefusal` and `explain` both use.

**Theme (slice 14).** `app/theme.ts` keeps System / Light / Dark under localStorage `bss.theme`
(every access in try/catch; a throwing storage means System) and sets `data-theme` on `<html>`
(System removes it, so `prefers-color-scheme` in `tokens.css` decides). `index.html` applies the
saved value in an inline script before first paint. The choice lives in the operator menu
(`ThemeChoice`, `data-ui="ui.shell.theme"`). `make web-shots` also writes `-dark` PNGs.
