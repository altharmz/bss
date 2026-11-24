Single-choice radio; group several and manage the selected value in the parent.
```jsx
{["Weekly","Biweekly"].map(v => <Radio key={v} value={v} label={v} checked={c===v} onChange={setC} />)}
```
