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
