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
`../ui/kit` (`Modal`, `PageTitle`, `Panel`, `Pill`, `Button`, ...), `../ui/states`
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

`tests/test_ui_requests.py` sends the same request strings to the real API with the
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
- Row N of `docs/next-slices.md`: `done: <commit subject>`.

## Ledger v2 styles and visual references

`ui/base.css` imports `tokens.css`, temporary `legacy-tokens.css`, then the five font
faces. New CSS uses the Ledger role and type tokens; legacy aliases bridge screens until
the cleanup slice. `ui/styleGuard.test.ts` checks CSS literals, font weights and alias
coverage. `make web-shots` captures seeded pages at both design widths without billing
writes; screenshot output is ignored.

### Ledger controls (slice 2a)

`Button` supports primary, secondary, danger and quiet variants, `small`, quiet
`tone="negative"`, and `busy`/`busyLabel`. Busy disables the button and adds its spinner.
The legacy danger-outline variant remains for the screen migrations in 2b.
`Field` wraps its control in a label, with optional `help` and `error`. `SelectField`,
`DateField`, `MoneyField` (string value and currency suffix) and `Checkbox` preserve native
handlers. `TextInput` retains its `mono` and runtime `width` API.
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
existing Actions accessible name. `defaultOpen` supports the gallery's open sample.
`TableRow` accepts `actions: MenuItem[]` for a `kind="actions"` cell; menu clicks do
not activate the row. `TableFooter` accepts `range: { from, to, total }`, with numeric
range emphasis; existing `left`/`right` slots remain for screen migration and paging.

### Ledger notes and failure timestamps (slice 3b2a)

`Note({ tone, lead?, code?, children })` supports info, warning and negative tones.
Negative notes have role alert; the other tones have role note. Leads are bold,
server codes are mono ink-3, and each tone has its own decorative SVG glyph.
The gallery previews all three tones. Existing screen callers migrate in 3b2b.
`ErrorBlock` captures each error's failure instant once, displaying HH:MM:SS in the
system context's business zone. Bootstrap errors preserve their call and Retry when
context is unavailable, omitting the timestamp until context exists.

### Ledger summary foundations (slice 3b2b1)

`PageHeader({ title, count?, meta?, actions?, filters? })` supplies the title/count row,
right actions, metadata and wrapping filter row. `PageTitle` delegates to it until slice 14.
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
uses fixed 400/560/760 px sizes, a padded body and subtle footer. Its context line sits
under the title; the step follows the title. `preview` renders static gallery examples
without an overlay or Escape handler. `Modal` temporarily delegates to Dialog, mapping
widths ≤440 to S, ≤620 to M, and larger widths to L; existing `subtitle` becomes context.
Existing callers' `bss-modal-body` containers own the same 20 px body spacing without
double padding. Escape, overlay clicks and the close button preserve closing behavior.
