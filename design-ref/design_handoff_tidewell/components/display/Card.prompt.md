Base container: white card with 1px hairline and 14px radius; elevation comes from borders, shadows only on hover/overlay.
```jsx
<Card><h3>Launch plan</h3><p>Due Friday</p></Card>
<Card variant="dark" padding={32}>…</Card>
<Card interactive onClick={open}>Task</Card>
```
- `soft` for inset regions on white, `dark` for one emphasis block per view, `outline` (dashed) for empty/drop zones.
